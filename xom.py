# xom.py
#
# Copyright (c) 2017 Oak Ridge National Laboratory.
# All rights reserved.
# See file LICENSE that is included with this distribution.
#
# @author Klemen Vodopivec

"""
XML Object Mapping module (aka xom) provides methods and base
class functionality to map XML DOM to objects. The idea was
borrowed from dexml but since we only ever need parsing existing
XML documents with OPI specific XML structure, this simplified
module was created.
"""

import xml
import copy

class Entity(object):
    """ Base object entity, to be extended by Model or Field. """

    _tagname = None
    _case_sensitive = False

    def __init__(self, **kwds):
        if "tagname" in kwds:
            self._tagname = kwds["tagname"]
        if "case_sensitive" in kwds:
            self._case_sensitive = kwds["case_sensitive"]

    def checkTagName(self, node, tagname=None):
        if tagname is None:
            if self._tagname is None:
                return False
            tagname = self._tagname

        if self._case_sensitive:
            return tagname == node.nodeName
        else:
            return tagname.lower() == node.nodeName.lower()

    def setTagName(self, tagname, overwrite=False):
        if self._tagname is None or overwrite:
            self._tagname = tagname

    def getTagName(self):
        return self._tagname

class Model(Entity):
    """ Model represents a group of fields for a given XML node. """

    _valid = False

    def __init__(self, **kwds):
        super(Model, self).__init__(**kwds)

        self._entities = {}

        for fieldname in dir(self):
            value = getattr(self, fieldname)
            if isinstance(value, Entity):
                entity = copy.deepcopy(value)
                tagname = entity.getTagName()
                if tagname is None:
                    tagname = fieldname
                    entity.setTagName(fieldname)

                self.setField(fieldname, entity, tagname)

    def setField(self, fieldname, value, tagname=None):
        """ Sets or updates the field handled by this class.

        When tagname is not provided, fieldname is used instead.
        """
        if tagname is None:
            tagname = fieldname
        if tagname not in self._entities:
            self._entities[tagname] = [ (fieldname,value) ]
        else:
            self._entities[tagname].append( (fieldname,value) )

        setattr(self, fieldname, value)

    def parse(self, node):

        # Check whether we should parse this node
        if not self.checkTagName(node):
            raise ValueError("<{0}> Model tagname mismatch, expecting {1}".format(node.nodeName, self.getTagName()))

        # Parse tag names
        child = node.firstChild
        while child is not None:
            tagname = child.nodeName

            entities = self._entities.get(tagname, [])
            for fieldname,entity in entities:
                if isinstance(entity, Entity):
                    try:
                        if entity.parse(child) and isinstance(entity, Field):
                            # Now replace Field with a Python type variable
                            # so that we don't have to implement __repr__, __cmp__
                            # and others.
                            setattr(self, fieldname, entity.get())

                    except Exception, e:
                        pass

            child = child.nextSibling

        # Parse this models' attributes
        for fieldname,entity in self._entities.get("__self__", []):
            for name,value in node.attributes.items():
                if isinstance(entity, Field):
                    nodeName = node.nodeName
                    node.nodeName = "__self__"
                    try:
                        if entity.parse(node):
                            # Now replace Field with a Python type variable
                            # so that we don't have to implement __repr__, __cmp__
                            # and others.
                            setattr(self, fieldname, entity.get())

                    except Exception, e:
                        pass
                    node.nodeName = nodeName

        # Now check that all required fields have been populated
        for tagname,entities in self._entities.items():
            for fieldname,entity in entities:
                if isinstance(entity, Field):
                    # entity.get() will throw if not set
                    setattr(self, fieldname, entity.get())

        # No exception raised, we're done
        self._valid = True

        return True

    def isValid(self):
        return self._valid

    def toDict(self, traverse=True):
        """ Returns a dictionary with of all the fields and their values.

        When traverse is True, recursively dives into all children. """
        d = {}
        for tagname,entities in self._entities.items():
            for fieldname,entity in entities:
                value = getattr(self, fieldname)
                if traverse and isinstance(value, Model):
                    d[fieldname] = value.toDict()
                else:
                    d[fieldname] = value
        return d

class Field(Entity):
    """ Represents single value XML node. """

    _attrname = None

    def __init__(self, **kwds):
        if "default" in kwds:
            # Set default value, make field optional
            self._value = kwds["default"]
        if "attrname" in kwds:
            self._attrname = kwds["attrname"]
        super(Field, self).__init__(**kwds)

    def setDefault(self, value):
        if "_value" not in dir(self):
            self._value = value

    def isValid(self):
        try:
            self.get()
            return True
        except AttributeError:
            return False

    def get(self):
        try:
            return self._value
        except AttributeError:
            raise AttributeError("[{0}] Missing required value".format(self._tagname))

    def parse(self, node):

        # Check that we should parse this node
        if not self.checkTagName(node):
            raise ValueError("<{0}> Field tagname mismatch, expecting {1}".format(node.nodeName, self.getTagName()))

        # Further Field specific checks for XML validity
        if node.nodeType != xml.dom.Node.ELEMENT_NODE:
            raise ValueError("<{0}> Expecting element node".format(node.nodeName))

        if self._attrname is None:
            if len(node.childNodes) != 1:
                raise ValueError("<{0}> Expecting 1 child node, got {1}".format(node.nodeName, len(node.childNodes)))
            child = node.childNodes[0]
            if child.nodeType != xml.dom.Node.TEXT_NODE:
                raise ValueError("<{0}> Expecting text node".format(node.nodeName))

            value = child.nodeValue

        else:
            for key,val in node.attributes.items():
                if key == self._attrname:
                    value = val
                    break
            else:
                raise ValueError("<{0}> Missing attribute '{1}'".format(node.nodeName, self._attrname))

        # Let derived classes handle the casting
        try:
            self._value = self._getNativeValue(value)
        except Exception, e:
            raise ValueError("<{0}> Failed to cast value: {1}".format(node.nodeName, e))

        return True

    def _getNativeValue(self, value):
        return value

class String(Field):
    """ Represents a text field. """
    pass

class Boolean(Field):
    def _getNativeValue(self, value):
        if value.lower() in [ "false", "0" ]:
            return False
        if value.lower() in [ "true", "1" ]:
            return True
        raise ValueError("Not a boolean value")

class Integer(Field):
    def _getNativeValue(self, value):
        return int(value)

class Float(Field):
    def _getNativeValue(self, value):
        return float(value)

class Number(Field):
    def _getNativeValue(self, value):
        try:
            return int(value)
        except ValueError:
            return float(value)

class Enum(Field):
    def __init__(self, values, **kwds):
        self._values = values
        if "default" not in kwds:
            kwds["default"] = 0
        super(Enum, self).__init__(**kwds)

    def _getNativeValue(self, value):
        index = int(value)
        return self._values[index]

class List(Field):
    def __init__(self, flat, **kwds):
        self._flat = flat
        kwds["default"] = []
        super(List, self).__init__(**kwds)

    def __iter__(self):
        for elem in self._value:
            yield elem

    def parse(self, node, tagname=None):

        # Check that we should parse this node
        if not self.checkTagName(node, tagname):
            raise ValueError("<{0}> Tagname mismatch, expecting {1}".format(node.nodeName, self.getTagName()))

        if node.nodeType != xml.dom.Node.ELEMENT_NODE:
            raise ValueError("<{0}> Expecting element node".format(node.nodeName))

        if self._flat:
            children = [ node ]
        else:
            children = []
            child = node.firstChild
            while child is not None:
                children.append(child)
                child = child.nextSibling

        for child in children:
            try:
                element = self._getInstance(child)
                element.parse(child)
                # TODO: what if element is Field, we need to cast it with .get()?
                if element.isValid():
                    self._value.append(element)
            except Exception, e:
                pass

        return False

    def _getInstance(self, classname):
        raise ValueError("<{0}> Class '{1}' not implemented".format(node.nodeName, classname))
