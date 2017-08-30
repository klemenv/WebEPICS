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
    function getAttrValue(attr, msg) {
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

        // Enum hack, provide a combined numeric field. If enum field is detected,
        // use it's index, otherwise match the value.
        if ("valueEnum" in rsp) {
            rsp.valueNum = rsp.valueEnum.index;
        } else if ($.isNumeric(rsp.value)) {
            rsp.valueNum = rsp.value;
        } else {
            rsp.valueNum = -1;
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

    /**
     * Simplified sprintf-like function.
     * Only takes one argument. Limited specifiers: d, f, x
     */
    function sprintf(fmt, value) {
        if (typeof(value) === "number") {
            var re = /%\.?([0-9]*)([dfx])(.*)/, match;
            if (match = re.exec(fmt)) {
                var format = match[2];
                var suffix = match[3];
                var precision = parseInt(match[1], 10);
                if (isNaN(precision)) {
                    precision = 3;
                } else {
                    precision = Math.min(21, Math.max(1, precision+1));
                }

                if (format == "b" || format == "s") {
                    format = (Number.isInteger(value) ? "d" : "f");
                }

                switch (format) {
                    case "d":
                        return Math.round(value).toString() + suffix;
                    case "f":
                        return value.toPrecision(precision).toString() + suffix;
                    case "x":
                        // TODO: ignored spacing specification
                        return "0x" + value.toString(16) + suffix;
                }
            }
            return value.toString();
        }
        return value;
    }

    /**
     * Re-format value into a string according to requested rules.
     *
     * Numerical values take into account precision and unit. Strings are
     * returned as is.
     */
    function valueFormat(value, element, debug=false) {
        switch (typeof(value)) {
            case "number":
                if (debug) console.log("value: " + value);

                var fmt = "%f";
                if ("format" in element.data()) {
                    fmt = element.data("format");
                }
                if (debug) console.log("fmt: " + fmt);
                if ("unit" in element.data()) {
                    if (debug) console.log("unit: " + element.data("unit"));
                    fmt += " " + element.data("unit");
                }
                ret = sprintf(fmt, value);
                if (debug) console.log("output: " + ret);
                return ret;
            case "string":
            default:
                return value;
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
                    var val = getAttrValue(match[1], rsp);
                    if (action == "js" && typeof(val) === "string") {
                        value = value.replace(match[0], "\"" + val + "\"");
                    } else if (match[1] == "value") {
                        value = value.replace(match[0], valueFormat(val, el)+"");
                    } else {
                        value = value.replace(match[0], val+"");
                    }
                } catch (e) {
                    value = value.replace(match[0], "undefined");
                }
            }

            switch(action) {
                case "text":
                    el.text(value);
                    break;
                case "value":
                    el.val(value);
                    break;
                case "css":
                    el.removeClass().addClass(value);
                    break;
                case "title":
                    el.prop("title", value);
                    break;
                case "format":
                    if (value !== "undefined") {
                        el.data("format", value);
                    }
                    break;
                case "units":
                    if (value !== "undefined") {
                        el.data("unit", value);
                    }
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
