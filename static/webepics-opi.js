// Requires jQuery

// Attempt with jQuery
$(document).ready(function() {

    // Manually force all PVs to be disconnected until connected to
    // WebSocket server
    processOnDisconnect();

    var cachedPVs = {};
    if (typeof(ws_url) !== "undefined") {
        if (typeof(debug) !== "boolean")
            debug = false;
        createWebSocket(ws_url, 1000, debug);
    }

    $("[data-action").click(function() {
        eval($(this).data("action"));
    });

    /**
     * Create WebSocket connection handle and initiate connect.
     * If connection to the server fails it automatically retries
     * until connected.
     * When connection is established, processOnConnect() function
     * is invoked.
     * Received messages are validated to be JSON and then passed
     * to processing function.
     * If connection is closed for whatever reason, reconnect is
     * scheduled.
     */
    function createWebSocket(ws_url, connect_delay=1000, debug=false) {
        wsHandle = new WebSocket(ws_url);
        connected = false;

        wsHandle.onopen = function() {
            connected = true;
            console.log('Connected to WebSocket server ' + ws_url);
            processOnConnect();
        };
        wsHandle.onclose = function() {
            console.log('Closed WebSocket connection');
            wsHandle = null;
            connected = false;
            processOnDisconnect();

            console.log("Try to reconnect in " + (connect_delay/1000.0).toString() + " seconds");
            var next_delay = Math.min(10000, Math.max(1000, connect_delay*2));
            setTimeout(function() { createWebSocket(ws_url, next_delay, debug) }, connect_delay);
        };
        wsHandle.onerror = function(error) {
            if (connected) console.log('WebSocket connection error:' + error);
        };
        wsHandle.onmessage = function(event) {
            if (debug) console.log('Received an update from WebSocket ' + event.data);

            var message = jQuery.parseJSON(event.data);
            if ("rsp" in message) {
                switch (message.rsp) {
                    case "pv_update":
                        processPvUpdate(message);
                        break;
                    default:
                        // Ignoring unknown response
                        break;
                }
            }
        };
    }

    /* Return selected attribute value from response message. */
    function getValue(attr, msg) {
        var value = msg;
        var fields = attr.split(".");
        for (i=0; i<fields.length; i++) {
            if (!(fields[i] in value)) {
                throw new RangeError("No such field in this message");
            }
            value = value[fields[i]];
        }
        return value;
    }

    function processOnConnect() {
        $("div[data-pv]").each(function() {
            var pv = $(this).data("pv");

            if (pv.length > 0) {
                // When no protocol is specified, default to CA
                if (pv.split("://").length == 1)
                    pv = "ca://" + pv;

                var req = {
                    pv: pv,
                    req: "pv_subscribe"
                };
                wsHandle.send(JSON.stringify(req));
                if (debug) console.log("Sent: " + JSON.stringify(req));
            }
        });
    }

    function processOnDisconnect() {
        var fake_rsp = { "alarm": { "severity": 4 } };
        // Select only widgets that have non-empty PV
        $("[data-pv!=''][data-pv] *[data-map]").each(function() {
            elementProcessMapping($(this), fake_rsp);
        });
    }

    function processPvUpdate(rsp) {
        var update = rsp;
        if (rsp.pv in cachedPVs) {
            $.extend(cachedPVs[rsp.pv], rsp);
            update = cachedPVs[rsp.pv];
        } else {
            cachedPVs[rsp.pv] = rsp;
        }

        // Select all widgets subscribed to this PV and apply mapped actions on sub-elements
        $("[data-pv='" + rsp.pv + "'] *[data-map]").each(function() {
            elementProcessMapping($(this), update);
        });

        // Backward compatibility when protocol was not part of PV url
        if (rsp.pv.split("://")[0] == "ca") {
            var pv = rsp.pv.split("://")[1];
            $("[data-pv='" + pv + "'] *[data-map]").each(function() {
                elementProcessMapping($(this), update);
            });
        }
    }

    function elementProcessMapping(el, rsp) {
        var re = /%([^%]*)%/, match;
        var mappings = el.data("map").split(";");

        for (var i=0; i<mappings.length; i++) {
            var tokens = mappings[i].split(":");
            var action = tokens[0].trim();
            var value = tokens.slice(1).join(":").trim();

            while (match = re.exec(value)) {
                try {
                    var val = getValue(match[1], rsp);
                    if (action == "js" && typeof val === "string") {
                        value = value.replace(match[0], "\"" + val.toString() + "\"");
                    } else {
                        value = value.replace(match[0], val.toString());
                    }
                } catch (e) {
                    value = value.replace(match[0], "undefined");
                }
            }

            switch(action) {
                case "text":
                    el.text(value);
                    break;
                case "css":
                    el.removeClass().addClass(value);
                    break;
                case "title":
                    el.prop("title", value);
                    break;
                case "js":
                    var element = el;
                    eval(value);
                    break;
                default:
                    break;
            }
        }
    }

    function elementSetVisible(element, visible) {
        (visible) ? element.show() : element.hide();
    }

    function elementToggleBorder(element, visible) {
        if (visible) {
            console.log("Enabling border");
            element.css("border", element.data("border"));
        } else {
            console.log("Disabling border");
            if (element.data("border") == undefined)
                element.data("border", element.css("border"));
            element.css("border", "");
        }
    }

    function setPvValue(pv, value) {
        // When no protocol is specified, default to CA
        if (pv.split("://").length == 1)
            pv = "ca://" + pv;

        var req = {
            pv: pv,
            req: "pv_put",
            value: value
        };
        wsHandle.send(JSON.stringify(req));
        if (debug) console.log("Sent: " + JSON.stringify(req));
    }
});