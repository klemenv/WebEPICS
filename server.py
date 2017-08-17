# server.py
#
# Copyright (c) 2017 Oak Ridge National Laboratory.
# All rights reserved.
# See file LICENSE that is included with this distribution.
#
# @author Klemen Vodopivec
#
import tornado.web
import tornado.httpserver

import argparse
import logging
import sys

import config
from common import WebEpicsError, WebEpicsWarning
from convert import ConvertHandler
from websocket import WebSocketHandler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebEPICS web-server")
    parser.add_argument("-c", "--config", help="Configuration file", default="webepics.conf")
    args = parser.parse_args()

    # Make it command line configurable
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)

    # Load configuration
    try:
        cfg = config.load(args.config)
    except WebEpicsError, e:
        logging.critical(str(e))
        sys.exit(1)

    # Create single context - instantiate FileLoader, Cache etc.
    convert_ctx = ConvertHandler.createContext(cfg["convert"])

    settings = cfg["tornado"]
    app = tornado.web.Application([
        (r"/ws", WebSocketHandler), # , {"websocket_max_message_size": 4096, "websocket_ping_interval": 30 }),
        (r"/static/(.*)", tornado.web.StaticFileHandler, cfg["static_web"]),
        (r"/.*", ConvertHandler, dict(ctx=convert_ctx))
        ], **settings)

    server = tornado.httpserver.HTTPServer(app)
    server.bind(cfg["server"]["port"])
    server.start(cfg["server"]["threads"])
    tornado.ioloop.IOLoop.current().start()
