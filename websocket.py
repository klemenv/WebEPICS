# websocket.py
#
# Copyright (c) 2017 Oak Ridge National Laboratory.
# All rights reserved.
# See file LICENSE that is included with this distribution.
#
# @author Klemen Vodopivec
#

import pvaccess

import json
import logging
import math

from common import WebEpicsError, WebEpicsWarning

import tornado.gen
import tornado.locks
import tornado.websocket

log = logging.getLogger(__name__)

# Dictionary of (yet to be) connected PVs, where key is PV name and value is object.
# Same PVs can be used by different clients.
pvs = {}

class PV():
    """ PV class manages connection to one PV. """
    def __init__(self, pvurl):
        self._pvurl = pvurl
        protocol, self._pvname = pvurl.split("://", 1)
        if protocol == "pva":
            self._protocol = pvaccess.PVA
        elif protocol == "ca":
            self._protocol = pvaccess.CA
        else:
            raise TypeError("Unsupported protocol {0}".format(protocol))

        if len(self._pvname) == 0:
            raise TypeError("Empty PV string")

        self._ch = None
        self._clients = []
        self._cached = {}

    def subscribe(self, client):
        """ Subscribes WebSocket client to receive this PV's updates. """
        if client not in self._clients:
            self._clients.append(client)
            #log.info("Subscribed client: {0}".format(client.getClientId()))

        if self._ch is None:
            print self._pvname
            self._ch = pvaccess.Channel(self._pvname, self._protocol)
            self._ch.subscribe("webepics", self.updateCb)
            self._ch.startMonitor("field()")

        if self._cached:
            # Monitor is already active and it won't send full PV structure once it connects,
            # but the protocol requires client receives full update on connect so do it here
            try:
                self.sendToClients(self._cached, [client])
            except tornado.websocket.WebSocketClosedError:
                pass

    def unsubscribe(self, client):
        """ Unsubscribes clients from further receiving updates for this PV. """
        try:
            self._clients.remove(client)
            log.info("Unsubscribed client: {0}".format(client.getClientId()))
            if not self._clients:
                self._ch.stopMonitor()
                self._ch.unsubscribe("webepics")
                self._ch = None
                self._cached = {}
                log.debug("No more clients, stopped monitor")
            return True
        except:
            return False

    def put(self, value):
        """ Request a put command with a new value. """

        ch = pvaccess.Channel(self._pvname, self._protocol)
        ch.put(value)

    def sendToClients(self, message, clientList=[]):
        """ Send response message to clients in list or all subscribed clients if list is empty.

        When a stalled or disconnected client is detected, it's automatically
        deleted from subscribed list. message can be a JSON string or a
        dictionary that will be turned to JSON string.
        """

        if not clientList:
            clientList = self._clients

        closedClients = []
        for client in clientList:
            try:
                client.onPvUpdate(self._pvurl, message)
            except tornado.websocket.WebSocketClosedError:
                log.debug("Removing disconnected client")
                closedClients.append(client)

        for client in closedClients:
            self.unsubscribe(client)

    def updateCb(self, pv):
        """ Callback function called when any PV field changes. """
        try:
            d = pv.toDict()

            # Enum workaround - rename existing value field to valueEnum,
            #                   add value field with resolved string
            # This way client will get updated value and index, but can also
            # get choices the first time
            try:
                # First extract value, can throw and skip the rest of code block
                value = d["value"]["choices"][ d["value"]["index"] ]
                d["valueEnum"] = d["value"]
                d["value"] = value
            except TypeError:
                pass

            # Calculate the differencies from previous update
            update = self._calcPvDiff(self._cached, d)
            update = self._jsonVerify(update)
            self._cached = d
        except Exception, e:
            log.warn("Failed to parse PV update: {0}".format(str(e)))
            return

        try:
            self.sendToClients(update)
        except Exception, e:
            log.warn("Failed to send update to clients: {0}".format(str(e)))
            return

    def _calcPvDiff(self, old, new, level=0):
        """ Return a copy of new with missing keys filled in from old. """
        d = {}
        if level < 3:
            for key in new.keys():
                if key not in old:
                    d[key] = new[key]
                elif isinstance(new[key], dict):
                    d1 = self._calcPvDiff(old[key], new[key], level+1)
                    if d1:
                        d[key] = d1
                elif old[key] != new[key]:
                    d[key] = new[key]
        return d

    def _jsonVerify(self, d):
        """ Recursively checks and fixes dictionary to be JSON compliant.

        NaN, +Inf, -Inf are turned into None.
        """
        for key in d.keys():
            if isinstance(d[key], dict):
                d[key] = self._jsonVerify(d[key])
            elif isinstance(d[key], float) and (math.isnan(d[key]) or math.isinf(d[key])):
                d[key] = None
        return d

class WebSocketHandler(tornado.websocket.WebSocketHandler):
    def check_origin(self, origin):
        """ Overloaded function to skip checking cross-origin.

        Make sure to read https://devcenter.heroku.com/articles/websocket-security
        for potential implications.
        """
        return True

    def open(self):
        """ Overload open method. """

        # Map of pvurl, pvinfo pairs
        self._pvs = {}

        self._lock = tornado.locks.Lock()

    def on_close(self):
        """ Overload on_close method. """
        for _, pvinfo in self._pvs.iteritems():
            pvinfo["pv"].unsubscribe(self)

    def on_message(self, message):
        try:
            message = json.loads(message)
        except:
            log.warn("Invalid request: failed to decode JSON request")
            return

        self.handleRequest(message)

    def handleRequest(self, message):
        """ Parse, verify and process the request from client. """

        if "pv" not in message:
            log.warn("Invalid request: missing 'pv' field")
            return
        try:
            _, _ = message["pv"].split("://")
        except ValueError:
            log.warn("Invalid request: invalid 'pv' field")
            return False

        # Verify mandatory fields
        if "req" not in message:
            log.warn("Invalid request: missing 'req' field")
            return

        try:
            if   message["req"] == "pv_subscribe":
                self._reqSubscribe(message)
            elif message["req"] == "pv_unsubscribe":
                self._reqUnsubscribe(message)
            elif message["req"] == "pv_get":
                self._reqGet(message)
            elif message["req"] == "pv_put":
                self._reqPut(message)
            else:
                log.warn("Unknown request: {0}".format(message["req"]))
        except Exception, e:
            log.error("Request failed: {0}".format(str(e)))

    @tornado.gen.coroutine
    def onPvUpdate(self, pvurl, update):
        """ Send response to the client. """
        if pvurl in self._pvs:
            if self._pvs[pvurl]["one_time"]:
                self._pvs[pvurl]["pv"].unsubscribe(self)
                del self._pvs[pvurl]

            rsp = dict(update)
            rsp["pv"] = pvurl
            rsp["rsp"] = "pv_update"

            # Need to serialize writes to client, as monitor callback may
            # be invoked form different threads
            with (yield self._lock.acquire()):
                self.write_message(rsp)

    def getClientId(self):
        """ Return non-unique client ID useful for log. """
        x_real_ip = self.request.headers.get("X-Real-IP")
        remote_ip = x_real_ip or self.request.remote_ip
        return remote_ip

    def _reqSubscribe(self, message, one_time=False):
        """ Handles subscribe message. """

        if message["pv"] in self._pvs:
            if not one_time:
                self._pvs[ message["pv"] ]["one_time"] = False
        else:
            # Create a PV object
            if message["pv"] in pvs:
                pv = pvs[ message["pv"] ]
            else:
                pv = PV(str(message["pv"]))
                pvs[ message["pv"] ] = pv

            self._pvs[ message["pv"] ] = {
                "pv": pv,
                "one_time": one_time,
                "rate_limit": 0
            }

            # Subscribe to updates
            pv.subscribe(self)

    def _reqUnsubscribe(self, message):
        """ Handles pv_subscribe message. """
        pvinfo = self._pvs.pop(message["pv"], None)
        if pvinfo is not None:
            pvinfo["pv"].unsubscribe(self)

    def _reqGet(self, message):
        """ Handles pv_get message. """

        if message["pv"] not in self._pvs:
            self._reqSubscribe(message, one_time=True)

    def _reqPut(self, message):
        """ Handles pv_set message. """

        rsp = {
            "pv": message["pv"] if "pv" in message else "undefined",
            "rsp": "pv_put"
        }

        print message
        if "pv" not in message or "value" not in message:
            rsp["status"] = "error: invalid request"
        else:
            # TODO: this needs to be asynchronized
            pv = PV(str(message["pv"]))
            pv.put(1)
            rsp["status"] = "success"

        print rsp

        #self.write_message(rsp)
