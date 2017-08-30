# bob.py
#
# Copyright (c) 2017 Oak Ridge National Laboratory.
# All rights reserved.
# See file LICENSE that is included with this distribution.
#
# @author Klemen Vodopivec

"""
BOB files handling and transforming to HTML.
"""

import xom
import xml.dom.minidom
import tornado.template
import re

import opi

BORDER_STYLES=['none', 'solid', 'groove', 'ridge', 'groove', 'ridge',
               'inset', 'outset', 'dotted', 'dashed', 'dashed', 'dashed',
               'solid', 'solid', 'solid', 'hidden' ]
HORIZONTAL_ALIGN=["left", "center", "right"]
VERTICAL_ALIGN=["top", "middle", "bottom"]
FORMAT_TYPES=["%{0}b", "%{0}d", "%{0}f", "%{0}x", "%{0}b", "%{0}x", "%{0}b", "%{0}f", "%{0}f", "%{0}f", "%{0}f"]

class Converter:
    """ BOB convert helper class.

    Implements getHtml() and replaceMacros() methods. Called from outside.
    """

    def __init__(self, templates_dir, caching):
        self.templates = tornado.template.Loader(templates_dir)
        self.macro_regex = re.compile("\$\([^\)]*\)", re.MULTILINE)
        self.caching = caching

    def replaceMacros(self, str, macros):
        return Macros.replace(str, macros)

    def getHtml(self, xml_str, macros={}):
        try:
            node = xml.dom.minidom.parseString(xml_str)
        except Exception, e:
            raise RuntimeError("Failed to parse BOB file: {0}".format(e))

        if len(node.childNodes) != 1:
            raise ValueError("<{0}> Expecting 1 child node, got {1}".format(node.nodeName, len(node.childNodes)))

        # Start with top-level Display widget
        display = Display()
        display.parse(node.documentElement)

        return self._renderWidget(display, macros)

    def _renderWidget(self, widget, macros, unique_id=1):

        # Help parser identify widgets
        widget.setField("unique_id", unique_id)

        # Make a copy that we can modify
        macros = dict(macros)

        # Create dictionary of all available macros
        try:
            include_parent_macros = widget.macros.include_parent_macros
            if not include_parent_macros:
                macros = dict()
            for macro,value in widget.macros.items():
                macros[macro] = Macros.replace(value, macros)
        except:
            include_parent_macros = True

        # Let widget handle parent macros
        widget.setParentMacros(macros)

        # Recursively render sub-widgets
        body = ""
        if "widgets" in dir(widget):
            for sub_widget in widget.widgets:
                unique_id += 1
                body += self._renderWidget(sub_widget, macros, unique_id)
        widget.setField("body", body)

        try:
            tmpl_name = widget.getType() + ".tmpl"
            tmpl = self.templates.load(tmpl_name)
        except IOError, e:
            try:
                tmpl_name = "Widget.tmpl"
                tmpl = self.templates.load(tmpl_name)
            except IOError, e:
                raise RuntimeError("Failed to load BOB template file '{0}': unsupported widget type {1}".format(tmpl_name, e.strerror))
        if not self.caching:
            self.templates.reset()

        # Generate HTML output
        try:
            html = tmpl.generate(**widget.toDict(traverse=False))
        except Exception, e:
            # Since there was an error in the template file, let user edit the file without restarting server
            self.templates.reset()

            raise RuntimeError("Failed to process BOB template file '{0}': {1}".format(tmpl_name, str(e)))

        # Replace available macros
        macros["pv_name"] = widget.pv_name if "pv_name" in dir(widget) else ""
        macros["pv_value"] = "%value%"
        if len(widget.actions) == 0:
             macros["actions"] = "no action"
        elif len(widget.actions) == 1:
             macros["actions"] = widget.actions[0].description
        else:
             macros["actions"] = "{0} actions".format(len(widget.actions))
        html = self.replaceMacros(html, macros)

        # At this point all hard-coded macros have been replaced and can be
        # saved in the cached file. The only ones left are dynamically
        # assigned ones which we'll have to assign at run time. Except if widget
        # refuses to do so.
        if not include_parent_macros:
            html = re.sub(self.macro_regex, "", html)

        return html

##############################################################################
### BOB widgets support classes, mostly extending xom Fields/Models with   ###
### custom behavior                                                        ###
##############################################################################

class WidgetList(xom.List):
    """ While the same as opi implementation, it searches in bob namespace. """

    def __init__(self, **kwds):
        super(WidgetList, self).__init__(True, **kwds)

    def parse(self, node):
        """ Make an instance of Widget based on XML node and append it to list.

        Reimplements method from xom.List class to allow double call to _getInstance().
        This is required to fall-back to base Widget class in case of un-supported
        widgets and display a user-friendly HTML object rather than skipping
        widget altogether.

        Always returns False.
        """

        # Check that we should parse this node
        if not self.checkTagName(node):
            raise ValueError("<{0}> WidgetList tagname mismatch, expecting {1}".format(node.nodeName, self.getTagName))

        if node.nodeType != xml.dom.Node.ELEMENT_NODE:
            raise ValueError("<{0}> Expecting element node".format(node.nodeName))

        try:
            widget = self._getInstance(node)
            widget.parse(node)
        except Exception, e:
            # Fall-back in case of not supported widget
            widget = Widget(tagname=node.nodeName)
            widget.parse(node)

        # TODO: what if element is Field, we need to cast it with .get()?
        if widget.isValid():
            self._value.append(widget)

        return False

    def _getInstance(self, node):
        """ Overloaded method to instantiate BOB widget based on typeId attribute. """
        classname = Widget.parseType(node)
        try:
            obj = globals()[classname](tagname=node.nodeName)
            if not isinstance(obj, Widget):
                raise ValueError("<{0}> Class '{1}' is not derived from Widget".format(node.nodeName, classname))
        except KeyError:
            obj = Widget(tagname=node.nodeName)
        return obj

class ActionList(opi.ActionList):
    def _getInstance(self, node):
        actionsMap = {
            "open_file": OpenFileAction,
            "open_display": OpenDisplayAction,
            "open_webpage": OpenWebpageAction,
            "write_pv": WritePvAction,
        }

        if node.attributes:
            for name,value in node.attributes.items():
                if name.lower() == "type":
                    if value in actionsMap:
                        obj = actionsMap[value]()
                    else:
                        obj = Action()
                    return obj
        raise ValueError("<{0}> Missing type attribute".format(node.nodeName))

class Color(opi.Color):
    pass

class Macros(opi.Macros):
    pass

class Action(opi.Action):
    pass

class WritePvAction(Action):
    pv_name = xom.String()
    value = xom.String()
    description = xom.String(default="")

class OpenPathAction(Action):
    path = xom.String()

    def parse(self, node):
        """ Overloaded method does all from base class plus escapes path parameter. """
        super(OpenPathAction, self).parse(node)

        self.path = tornado.escape.url_escape(self.path, False)

        return True

class OpenDisplayAction(OpenPathAction):
    target = xom.Enum(["replace", "tab", "window"])
    macros = Macros()

    def addMacros(self, macros):
        """ Overloaded method formats path field as URL and includes macros in query string. """

        m = {}
        if self.macros.include_parent_macros:
            m = dict(macros)
        # Same macros defined in widget overwrite parent macros
        for k,v in self.macros.items():
            m[k] = Macros.replace(v, m)

        if m:
            # Turn into a query string
            self.path += "?"
            escape = tornado.escape.url_escape
            self.path += "&".join("{0}={1}".format(escape(k), escape(v)) for k,v in m.iteritems())

class OpenFileAction(OpenPathAction):
    def __init__(self, **kwds):
        super(OpenFileAction, self).__init__(**kwds)
        self.path.setTagName("file", overwrite=True)
        self.setField("path", self.path, "file")

class OpenWebpageAction(OpenPathAction):
    def __init__(self, **kwds):
        super(OpenWebpageAction, self).__init__(**kwds)
        self.path.setTagName("url", overwrite=True)
        self.setField("path", self.path, "url")



##############################################################################
### BOB widgets classes                                                    ###
##############################################################################

class Display(xom.Model):
    """ Model for the main Display widget. """

    actions = ActionList()
    macros = Macros()
    name = xom.String(default="")
    widgets = WidgetList(tagname="widget", default=[])

    def __init__(self, **kwds):
        super(Display, self).__init__(tagname="display", **kwds)
        self.setField("widget_type", "Display")

    def getType(self):
        return "Display"

    def setParentMacros(self, macros):
        """ Called from converter to recognize additional macros provided by parent. """
        for action in self.actions:
            action.addMacros(macros)

class Widget(xom.Model):
    """ Base Widget model with common fields. """

    actions = ActionList()
    name = xom.String(default="")
    x = xom.Integer(default=0)
    y = xom.Integer(default=0)
    height = xom.Integer(default=-1)
    width = xom.Integer(default=-1)

    def __init__(self, typeId=None, **kwds):
        self._typeId = (self.__class__.__name__ if typeId is None else typeId)
        super(Widget, self).__init__(**kwds)
        self.setField("widget_type", self._typeId)

    @staticmethod
    def parseType(node):
        # Remap some container names to match code
        widgetMap = {
            "action_button"    : "ActionButton",
            "bool_button"      : "BoolButton",
            "checkbox"         : "CheckBox",
            "embedded"         : "Embedded",
            "group"            : "Group",
#            "label"            : "Label",
#            "textupdate"       : "TextUpdate",
        }

        for key,value in node.attributes.items():
            if key == "type":
                return widgetMap.get(value, value)
        else:
            raise ValueError("<{0}> Missing attribute type".format(node.nodeName))

    def getType(self):
        return self._typeId

    def parse(self, node):
        """ Overloaded function makes sure we're parsing <widget> node. """

        typeId = Widget.parseType(node)
        if self._typeId != typeId:
            if self._typeId != "Widget":
                raise ValueError("<{0}> Attribute type mismatch {1}!={2}".format(node.nodeName, typeId, self._typeId))
            self._typeId = typeId
            self.setField("widget_type", self._typeId)

        return super(Widget, self).parse(node)

    def setParentMacros(self, macros):
        """ Called from converter to recognize additional macros provided by parent. """
        pass

class ActionButton(Widget):
    background_color = Color()
    enabled = xom.Boolean(default=True)
    # font
    foreground_color = Color()
    height = xom.Integer(default=50)
    horizontal_alignment = xom.Enum(HORIZONTAL_ALIGN, default=0)
    push_action_index = xom.Integer(default=0)
    pv_name = xom.String(default="")
    pv_value = xom.String(default="")
    rotation = xom.Enum(["0deg", "90deg", "180deg", "270deg"])
    text = xom.String(default="")
    tooltip = xom.String(default="")
    visible = xom.Boolean(default=True)
    width = xom.Integer(default=100)

class BoolButton(Widget):
    alarm_border = xom.Boolean(default=True)
    background_color = Color()
    bit = xom.Integer(default=0)
    enabled = xom.Boolean(default=True)
    # font
    foreground_color = Color()
    height = xom.Integer(default=30)
    labels_from_pv = xom.Boolean(default=False)
    off_color = Color()
    off_label = xom.String(default="")
    on_color = Color()
    on_label = xom.String(default="")
    pv_name = xom.String(default="")
    pv_value = xom.String(default="")
    show_led = xom.Boolean(default=True)
    tooltip = xom.String(default="")
    visible = xom.Boolean(default=True)
    width = xom.Integer(default=100)

class CheckBox(Widget):
    alarm_border = xom.Boolean(default=True)
    bit = xom.Integer(default=0)
    enabled = xom.Boolean(default=True)
    # font
    height = xom.Integer(default=20)
    label = xom.String(default="")
    pv_name = xom.String(default="")
    pv_value = xom.String(default="")
    tooltip = xom.String(default="")
    visible = xom.Boolean(default=True)
    width = xom.Integer(default=100)

class Embedded(Widget):
    enabled = xom.Boolean(default=True)
    # font
    height = xom.Integer(default=200)
    group_name = xom.String(default="")
    macros = Macros()
    file = xom.String(default="")
    resize = xom.Enum(["none","content","widget"])
    tooltip = xom.String(default="")
    visible = xom.Boolean(default=True)
    width = xom.Integer(default=300)

    def setParentMacros(self, macros):
        """ Invoke base class function and also update opi_path field. """
        super(Embedded, self).setParentMacros(macros)

        m = {}
        if self.macros.include_parent_macros:
            m = dict(macros)
        # Same macros defined in widget overwrite parent macros
        for k,v in self.macros.items():
            m[k] = Macros.replace(v, m)

        if m and self.file:
            # Turn into a query string
            self.file += "?"
            escape = tornado.escape.url_escape
            self.file += "&".join("{0}={1}".format(escape(k), escape(v)) for k,v in m.iteritems())

class Group(Widget):
    background_color = Color()
    enabled = xom.Boolean(default=True)
    # font
    foreground_color = Color()
    height = xom.Integer(default=200)
    macros = Macros()
    style = xom.Enum(["group","title","border","none"])
    tooltip = xom.String(default="")
    visible = xom.Boolean(default=True)
    widgets = WidgetList(tagname="widget", default=[])
    width = xom.Integer(default=300)

class TextUpdate(Widget):
    """ TextUpdate widget handler. """

    background_color = Color()
    border_alarm_sensitive = xom.Boolean(default=False)
    border_color = Color()
    border_style = xom.Enum(BORDER_STYLES)
    border_width = xom.Integer(default=0)
    foreground_color = Color()
    format_type = xom.Integer(default=0)
    horizontal_alignment = xom.Enum(HORIZONTAL_ALIGN)
    precision = xom.Integer(default=0)
    precision_from_pv = xom.Boolean(default=True)
    pv_name = xom.String(default="")
    pv_value = xom.String(default="")
    show_units = xom.Boolean(default=True)
    text = xom.String(default="")
    tooltip = xom.String(default="")
    transparent = xom.Boolean(default=False)
    vertical_alignment = xom.Enum(VERTICAL_ALIGN)
    visible = xom.Boolean(default=True)
    wrap_words = xom.Boolean(default=True)

    def parse(self, node):
        done = super(TextUpdate, self).parse(node)

        if self.precision_from_pv:
            fmt = None
        else:
            try:
                fmt = FORMAT_TYPES[self.format_type]
            except:
                fmt = FORMAT_TYPES[0]

            precision = self.precision
            if self.format_type == 5:
                precision = 8

            fmt = fmt.format(precision)
        self.setField("value_format", fmt)

        return done

class Label(Widget):
    """ Label widget handler. """

    background_color = Color()
    border_color = Color()
    border_style = xom.Enum(BORDER_STYLES)
    border_width = xom.Integer(default=0)
    enabled = xom.Boolean(default=True)
    # font
    foreground_color = Color()
    horizontal_alignment = xom.Enum(HORIZONTAL_ALIGN, default=0)
    text = xom.String(default="")
    tooltip = xom.String(default="")
    transparent = xom.Boolean(default=False)
    vertical_alignment = xom.Enum(VERTICAL_ALIGN, default=0)
    visible = xom.Boolean(default=True)
    wrap_words = xom.Boolean(default=True)

class LED(Widget):
    background_color = Color()
    bit = xom.Integer(default=0)
    border_alarm_sensitive = xom.Boolean(default=False)
    border_color = Color()
    border_style = xom.Enum(BORDER_STYLES)
    border_width = xom.Integer(default=0)
    data_type = xom.Enum(["bit", "enum"])
    enabled = xom.Boolean(default=True)
    #font
    foreground_color = Color()
    pv_name = xom.String(default="")
    pv_value = xom.String(default="")
    show_boolean_label = xom.Boolean(default=False)
    square_led = xom.Boolean(default=False)
    state_count = xom.Integer(default=2)
    tooltip = xom.String(default="")
    visible = xom.Boolean(default=True)

    def parse(self, node):
        """ Turn LED XML nodes into a list of states.

        Handles multi-LED and 2-LED cases. Dynamically adds state_count and
        states fields to this Model. states field is sorted list of Models
        containing sub-fields value,color,label for each state.
        """
        super(LED, self).parse(node)

        class LEDState(xom.Model):
            value = xom.Number()
            label = xom.String(default="")
            color = Color()

            def parse(self, node):
                pass

        # Read number of total states, default to 2 for off/on LED
        count = xom.Integer(tagname="state_count", default=2)
        child = node.firstChild
        while child is not None:
            if child.nodeName == "state_count":
                count.parse(child)
                break
            child = child.nextSibling
        self.setField("state_count", count.get())

        # Create a list of all state models
        states = []
        for i in range(self.state_count):
            state = LEDState()
            states.append(state)

        # Parse nodes describing states - handles 2 state LED as well as n-state LED
        child = node.firstChild
        while child is not None:
            try:
                check, fieldname, i = child.nodeName.split("_")
                i = int(i)
                if check == "state":
                    if fieldname == "color":
                        states[i].color.setTagName(child.nodeName, True)
                        states[i].color.parse(child)
                    elif fieldname == "label":
                        states[i].label.setTagName(child.nodeName, True)
                        states[i].label.parse(child)
                    elif fieldname == "value":
                        states[i].value.setTagName(child.nodeName, True)
                        states[i].value.parse(child)
            except:
                if child.nodeName == "off_color":
                    states[0].color.setTagName(child.nodeName, True)
                    states[0].color.parse(child)
                    states[0].value.setDefault(0)
                elif child.nodeName == "off_label":
                    states[0].label.setTagName(child.nodeName, True)
                    states[0].label.parse(child)
                    states[0].value.setDefault(0)
                elif child.nodeName == "on_color":
                    states[1].color.setTagName(child.nodeName, True)
                    states[1].color.parse(child)
                    states[1].value.setDefault(1)
                elif child.nodeName == "on_label":
                    states[1].label.setTagName(child.nodeName, True)
                    states[1].label.parse(child)
                    states[1].value.setDefault(1)

            child = child.nextSibling

        # Transform fields into Python objects
        for i in range(self.state_count):
            states[i].setField("label", states[i].label.get())
            states[i].setField("value", states[i].value.get())

        # Assign 'states' field
        self.setField("states", states)
        return True

class TextInput(Widget):
    background_color = Color()
    border_alarm_sensitive = xom.Boolean(default=False)
    border_color = Color()
    border_style = xom.Enum(BORDER_STYLES)
    border_width = xom.Integer(default=0)
    enabled = xom.Boolean(default=True)
    #font
    foreground_color = Color()
    format_type = xom.Integer(default=0)
    horizontal_alignment = xom.Enum(HORIZONTAL_ALIGN)
    limits_from_pv = xom.Boolean(default=False)
    maximum = xom.Number(default=100)
    minimum = xom.Number(default=0)
    multiline_input = xom.Boolean(default=False)
    precision = xom.Integer(default=0)
    precision_from_pv = xom.Boolean(default=True)
    pv_name = xom.String(default="")
    pv_value = xom.String(default="")
    selector_type = xom.Enum(["none","file","datetime"])
    show_units = xom.Boolean(default=True)
    text = xom.String(default="")
    tooltip = xom.String(default="")
    transparent = xom.Boolean(default=False)
    visible = xom.Boolean(default=True)

    def parse(self, node):
        super(TextInput, self).parse(node)

        if self.precision_from_pv:
            fmt = None
        else:
            try:
                fmt = FORMAT_TYPES[self.format_type]
            except:
                fmt = FORMAT_TYPES[0]

            precision = self.precision
            if self.format_type == 5:
                precision = 8

            fmt = fmt.format(precision)
        self.setField("value_format", fmt)

        return True
