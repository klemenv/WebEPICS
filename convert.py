# convert.py
#
# Copyright (c) 2017 Oak Ridge National Laboratory.
# All rights reserved.
# See file LICENSE that is included with this distribution.
#
# @author Klemen Vodopivec
#
import common
import logging
import md5
import mimetypes
import os
import re
import time
import sys
import urllib2
import urlparse
import xml.sax.handler

from common import WebEpicsError, WebEpicsWarning

import tornado.template
import tornado.web
import tornado.escape

log = logging.getLogger(__name__)

def xml2obj(src):
    """
    A simple function to convert XML data into Python structure object.

    Copied from: http://code.activestate.com/recipes/534109-xml-to-python-data-structure/
    """

    non_id_char = re.compile('[^_0-9a-zA-Z]')
    def _name_mangle(name):
        return non_id_char.sub('_', name)

    class DataNode(object):
        def __init__(self):
            self._attrs = {}    # XML attributes and child elements
            self.data = None    # child text data
        def __len__(self):
            # treat single element as a list of 1
            return 1
        def __getitem__(self, key):
            if isinstance(key, basestring):
                return self._attrs.get(key,None)
            else:
                return [self][key]
        def __contains__(self, name):
            return self._attrs.has_key(name)
        def __nonzero__(self):
            return bool(self._attrs or self.data)
        def __getattr__(self, name):
            if name.startswith('__'):
                # need to do this for Python special methods???
                raise AttributeError(name)
            return self._attrs.get(name,None)
        def _add_xml_attr(self, name, value):
            # Try to convert field to native numbers adn booleans
            try:
                value = int(value)
            except:
                try:
                    value = float(value)
                except:
                    if value == "true":
                        value = True
                    elif value == "false":
                        value = False

            if name in self._attrs:
                # multiple attribute of the same name are represented by a list
                children = self._attrs[name]
                if not isinstance(children, list):
                    children = [children]
                    self._attrs[name] = children
                children.append(value)
            else:
                self._attrs[name] = value
        def _set_xml_attr(self, name, value):
            self._attrs[name] = value
        def __str__(self):
            return self.data or ''
        def __repr__(self):
            items = sorted(self._attrs.items())
            if self.data:
                items.append(('data', self.data))
            return u'{%s}' % ', '.join([u'%s:%s' % (k,repr(v)) for k,v in items])
        def toDict(self):
            return self._attrs

    class TreeBuilder(xml.sax.handler.ContentHandler):
        def __init__(self):
            self.stack = []
            self.root = DataNode()
            self.current = self.root
            self.text_parts = []
        def startElement(self, name, attrs):
            self.stack.append((self.current, self.text_parts))
            self.current = DataNode()
            self.text_parts = []
            # xml attributes --> python attributes
            for k, v in attrs.items():
                self.current._add_xml_attr(_name_mangle(k), v)
        def endElement(self, name):
            text = ''.join(self.text_parts).strip()
            if text:
                self.current.data = text
            if self.current._attrs:
                obj = self.current
            else:
                # a text only node is simply represented by the string
                obj = text or ''
            self.current, self.text_parts = self.stack.pop()
            self.current._add_xml_attr(_name_mangle(name), obj)
        def characters(self, content):
            self.text_parts.append(content)

    builder = TreeBuilder()
    if isinstance(src,basestring):
        xml.sax.parseString(src, builder)
    else:
        xml.sax.parse(src, builder)
    return builder.root._attrs.values()[0]

class OpiConverter:
    def __init__(self, templates_dir, caching):
        self.templates = tornado.template.Loader(templates_dir)
        self.macro_regex = re.compile("\$\([^\)]*\)", re.MULTILINE)
        self.caching = caching

        self._transformations = [
            self._trans2StateEnum,
            self._transActionsList,
        ]

    def replaceMacros(self, str, macros):
        """ Replaces macros with their actual values in given string. """
        for macro,value in macros.iteritems():
            str = re.sub("\$\({0}\)".format(macro), value, str, flags=re.MULTILINE)
        return str

    def getHtml(self, xml_str, macros={}):
        # Start with top-level Display widget
        try:
            widget = xml2obj(xml_str)
        except Exception, e:
            raise RuntimeError("Failed to parse OPI file: {0}".format(e))
        print widget

        try:
            if widget.typeId != "org.csstudio.opibuilder.Display":
                raise RunTimeError("")
        except:
            # Catch our RuntimeError() as well as non-existing typeId atribute
            raise RuntimeError("Invalid OPI file, missing or invalid typeId directive")

        return self._renderWidget(widget, macros)

    def _renderWidget(self, widget, macros, unique_id=1):
        # Make a copy that we can modify
        macros = dict(macros)

        try:
            tmpl_name = widget.widget_type.replace(" ", "") + ".tmpl"
        except:
            raise RuntimeError("Invalid OPI file, missing widget_type directive")

        try:
            replacements = dict(widget._attrs)
        except:
            raise RuntimeError("Invalid OPI file '{0}'".format(tmpl_name))

        # Help parser identify widgets
        replacements["unique_id"] = unique_id

        # Apply transformations
        for trans in self._transformations:
            replacements = trans(replacements, macros)

        # Create dictionary of all available macros
        try:
            include_parent_macros = widget.macros.include_parent_macros
            if not include_parent_macros:
                macros = dict()
            for macro,value in widget.macros._attrs.iteritems():
                if macro != "include_parent_macros":
                    macros[macro] = value
        except:
            include_parent_macros = True

        # Replace macros in macros
        # TODO!

        # Recursively render sub-widgets
        replacements['body'] = ""
        if widget.widget:
            for sub_widget in widget.widget:
                unique_id += 1
                replacements['body'] += self._renderWidget(sub_widget, macros, unique_id)

        try:
            tmpl = self.templates.load(tmpl_name)
            if not self.caching:
                self.templates.reset()
        except IOError, e:
            try:
                tmpl = self.templates.load("Unsupported.tmpl")
                if not self.caching:
                    self.templates.reset()
            except IOError, e:
                raise RuntimeError("Failed to load OPI template file '{0}': unsupported widget type {1}".format(tmpl_name, e.strerror))

        # Generate HTML output
        try:
            html = tmpl.generate(**replacements)
        except Exception, e:
            # Since there was an error in the template file, let user edit the file without restarting server
            self.templates.reset()

            raise RuntimeError("Failed to process OPI template file '{0}': {1}".format(tmpl_name, str(e)))

        # Replace available macros
        macros["pv_name"] = replacements["pv_name"] if "pv_name" in replacements else ""
        macros["pv_value"] = "%value%"
        macros["actions"] = ""
        if "actions" in replacements:
            if len(replacements["actions"]["action"]) == 0:
                macros["actions"] = "no action"
            elif len(replacements["actions"]["action"]) == 1:
                macros["actions"] = replacements["actions"]["action"][0]["description"]
            else:
                macros["actions"] = "{0} actions".format(len(replacements["actions"]["action"]))
        html = self.replaceMacros(html, macros)

        # At this point all hard-coded macros have been replaced and can be
        # saved in the cached file. The only ones left are dynamically
        # assigned ones which we'll have to assign at run time. Except if widget
        # refuses to do so.
        if not include_parent_macros:
            html = re.sub(self.macro_regex, "", html)

        return html

    def _trans2StateEnum(self, replacements, macros):
        """ Turn 2-state LED into multi-state with only two states. """
        if replacements["widget_type"] == "LED":
            if "on_color" in replacements and "off_color" in replacements:
                replacements["state_count"] = 2
                replacements["state_color_0"] = replacements["off_color"]
                replacements["state_label_0"] = replacements["off_label"]
                replacements["state_value_0"] = 0
                replacements["state_color_1"] = replacements["on_color"]
                replacements["state_label_1"] = replacements["on_label"]
                replacements["state_value_1"] = 1
        return replacements

    def _transActionsList(self, replacements, macros):
        """ Turn single action into list with 1 element. """

        if "actions" in replacements:
            l = []
            if "action" in replacements["actions"]:
                if isinstance(replacements["actions"]["action"], list):
                    l = replacements["actions"]["action"]
                else:
                    l = [ replacements["actions"]["action"] ]

                for action in l:
                    if action["type"] == "OPEN_DISPLAY":
                        url = tornado.escape.url_escape(action["path"], False)

                        # Treat macros here and do proper URL escaping
                        m = []
                        if action["macros"]["include_parent_macros"]:
                            m = dict(macros)
                        # Same macros defined in widget overwrite parent macros
                        for k,v in action["macros"].toDict().iteritems():
                            if k != "include_parent_macros":
                                m[k] = v

                        if m:
                            # Turn into a query string
                            url += "?"
                            escape = tornado.escape.url_escape
                            url += "&".join("{0}={1}".format(escape(k), escape(v)) for k,v in m.iteritems())

                        action._set_xml_attr("path", url)
                    elif action["type"] == "OPEN_FILE":
                        url = tornado.escape.url_escape(action["path"], False)
                        action._set_xml_attr("path", url)
                    elif action["type"] == "OPEN_WEBPAGE":
                        url = tornado.escape.url_escape(action["hyperlink"], False)
                        action._set_xml_attr("hyperlink", url)

            replacements["actions"]._set_xml_attr("action", l)
        return replacements

class FileCache:
    """ Local file storage abstraction class.

    Files are stored on local file disk for faster access. A local file name
    is generated from the requested file name. A tree hierarchy of up to 5
    levels is supported to disperse files into sub-directories when dealing
    with lots of files. File names passed to all functions can consist of 0 or
    more directories.
    """

    def __init__(self, path, depth=0):
        """ Initializes FileCache object.

        path parameter must be a local file system path. Non-absolute paths
        are relative to the location of currently running script. Non-existing
        directories are created automatically. OSError is raised when directory
        can not be created.
        """
        if not os.path.isabs(path):
            pwd = os.path.dirname(os.path.abspath(sys.argv[0]))
            path = os.path.join(pwd, path)
        try:
            os.makedirs(path, 0755)
        except OSError, e:
            if e.errno != 17:
                raise WebEpicsError("Failed to create directory: {0}".format(path))
            pass
        self.dir = path
        self.depth = min(5, max(0, depth))

    def getModifiedTime(self, filename):
        """ Return the time of last modification of filename as unix EPOCH, 0 when not cached. """
        try:
            return int(os.path.getmtime(self.getCachedPath(filename, False)))
        except:
            return 0

    def getCachedPath(self, filename, create_dirs=False):
        """ Return absolute path of (yet to be) cached file.

        Generates a unique path in local cache of the filename. The return path
        is relative to cache base path and it may contain up to 5
        sub-directories. When create_dirs parameter is True, missing
        directories are created.

        Raises OSError only when directory creation is requested and it failed.
        """
        hash = md5.new(filename).hexdigest()
        dir = self.dir

        # Create sub-folders based on hash name - only hex characters allowed
        for i in range(self.depth):
            dir = os.path.join(dir, hash[i])
            if create_dirs:
                try:
                    os.mkdir(dir, 0755)
                    log.debug("Created cached directory: {0}".format(dir))
                except OSError, e:
                    if e.errno == 17:
                        pass
                    raise WebEpicsError("Failed to create directory: {0}".format(path))

        return os.path.join(dir, hash)

    def save(self, filename, data):
        """ Save data to local cache for a given filename. """
        path = self.getCachedPath(filename, True)
        try:
            f = open(path, "w")
            f.write(data)
            f.close()
        except Exception as e:
            raise WebEpicsWarning("Failed to write cached file: {0} ({1})".format(filename, str(e)))
        log.debug("Cached {0} -> {1}".format(filename, path))

    def read(self, filename):
        """ Retrieve cached data of a given filename. """
        path = self.getCachedPath(filename, False)
        try:
            f = open(path, "r")
            data = f.read()
            f.close()
        except Exception as e:
            raise WebEpicsWarning("Failed to read cached file: {0} ({1})".format(filename, str(e)))

        log.debug("Read from cache {0} -> {1}".format(path, filename))
        return data


class FileLoader:
    """ Helper class for loading file contents from file system or URL.
    
    Class abstracts the way to work with local or remote files. 
    """

    def __init__(self, path, timeout=2.0):
        """ Initialize FileLoader class with user parameters.

        path can be an URL or a relative/absolute file system path. Only http
        and https URLs are supported. Function will raise RuntimeError exception
        when path parameter can not be parsed.
        Timeout is only applicable to URL paths. """
        self.timeout = timeout

        r = urlparse.urlparse(path)
        if r.scheme == "http" or r.scheme == "https":
            self.url = path + "/"
            self.dir = None
        elif r.scheme == "file" or r.scheme == "":
            path = r.path
            if not os.path.isabs(path):
                pwd = os.path.dirname(os.path.abspath(sys.argv[0]))
                path = os.path.join(pwd, path)
            if os.path.exists(path):
                self.dir = path
                self.url = None
            else:
                raise WebEpicsError("Failed to initialize FileLoader, base directory doesn't exist: {0}".format(path))
        else:
            raise WebEpicsError("Failed to initialize FileLoader, unsupported path: {0}".format(path))

    def get(self, filename, timeout=None):
        """ Return file contents.

        Loads a filename relative to the path location passed to constructor
        and return its contents. Raises RuntimeError when file can not be
        loaded.

        This is a blocking call that may take long time depending on the
        availability of the URL. Use timeout parameter to limit time to load.
        """

        if timeout is None:
            timeout = self.timeout

        if self.dir:
            path = os.path.normpath(os.path.join(self.dir, filename))
            if not path.startswith(self.dir):
                raise WebEpicsWarning("Requested path outside base directory: {0}".format(path), 404)
            try:
                f = open(path, "rb")
            except IOError, e:
                if e.errno == 2:
                    raise WebEpicsWarning("File not found: {0}".format(filename), 404)
                elif e.errno == 13:
                    raise WebEpicsWarning("Permission denied: {0}".format(filename), 403)
                else:
                    raise WebEpicsWarning("Failed to open file: {0}".format(filename))

        else:
            url = urlparse.urljoin(self.url, filename)
            try:
                f = urllib2.urlopen(url, timeout=timeout)
            except URLError:
                raise WebEpicsWarning("Failed to fetch file: {0}".format(url))

        try:
            return f.read()
        except Exception as e:
            raise WebEpicsWarning("Failed to read file: {0}".format(e))

    def getModifiedTime(self, filename, timeout=None):
        """ Return time when file was last modified as unix EPOCH. """

        if timeout is None:
            timeout = self.timeout

        if self.dir:
            path = os.path.normpath(os.path.join(self.dir, filename))
            if not path.startswith(self.dir):
                raise WebEpicsWarning("Requested path outside base directory: {0}".format(path), 404)
            try:
                return int(os.path.getmtime(path))
            except OSError, e:
                if e.errno == 2:
                    raise WebEpicsWarning("File not found: {0}".format(filename), 404)
                elif e.errno == 13:
                    raise WebEpicsWarning("Permission denied: {0}".format(filename), 403)
                else:
                    raise WebEpicsWarning("Failed to open file: {0}".format(filename))

        else:
            # TODO: need to re-design and use Tornado async mechanism
            url = urlparse.urljoin(self.url, filename)
            request = urllib2.Request(url)
            request.get_method = lambda: "HEAD"
            response = urllib2.urlopen(request, timeout=timeout)
            header = response.info().getheader("Last-Modified")
            return int(time.mktime(time.strptime(header, '%a, %d %b %Y %H:%M:%S GMT')))

    def isRemote(self):
        """ Return True if configured to load files from remote web server. """
        return not self.dir

    def getContentType(self, filename):
        """ Return Content-Type header to be used for this file. """
        
        if self.dir:
            path = os.path.normpath(os.path.join(self.dir, filename))
            if not path.startswith(self.dir):
                raise WebEpicsWarning("Requested path outside base directory: {0}".format(path), 404)

            mime_type, encoding = mimetypes.guess_type(path)

            if encoding == "gzip":
                return "application/gzip"
            elif encoding is None and mime_type is not None:
                return mime_type
            else:
                return "application/octet-stream"
        else:
            url = urlparse.urljoin(self.url, filename)
            request = urllib2.Request(url)
            request.get_method = lambda: "HEAD"
            response = urllib2.urlopen(request, timeout=self.timeout)
            header = response.info().getheader("Content-Type")

class ConvertHandler(tornado.web.RequestHandler):
    """ Extended Tornado handler for converting files on-the-fly.

    Supported non-html files can be converted using this handler. They will be
    parsed and turned into HTML format.
    """

    @staticmethod
    def createContext(cfg):
        """ Create context to be used by all ConvertHandler instancies.

        This function creates a single context that can be used by multiple
        ConvertHandler instancies. It instantiates FileLoader, Cache and all
        supported file converter classes.
        """

        ctx = {}

        if "files" not in cfg or "path" not in cfg["files"]:
            raise common.WebEpicsError("Configuration error: invalid 'files' setup")
        ctx["loader"] = FileLoader(cfg["files"]["path"])
        log.info("Convertable files path: {0}".format(cfg["files"]["path"]))

        if "cache" not in cfg or "path" not in cfg["cache"] or cfg["cache"]["path"] is "":
            log.warn("Caching disabled")
            ctx["cache"] = None
        else:
            ctx["cache"] = FileCache(cfg["cache"]["path"])
            log.info("Cache base dir: {0}".format(cfg["cache"]["path"]))

        # Setup converters
        ctx["converters"] = {}
        if "opi" in cfg:
            if "templates" not in cfg["opi"]:
                log.error("Configuration error: missing 'opi/templates' directive")
            else:
                ctx["converters"]["opi"] = OpiConverter(cfg["opi"]["templates"], ctx["cache"] != None)
                log.info("Loaded .opi file converter")

        return ctx

    def initialize(self, ctx):
        """ Called by Tornado before every request, the only place to pass in ctx. """
        self.loader = ctx["loader"]
        self.cache = ctx["cache"]
        self.converters = ctx["converters"]

    def get(self):
        """ Overloaded Tornado get() handler.

        Files that can be converted to HTML are handled locally. All other
        files are redirected to be handled by Tornado directly or redirected
        to external web server.

        Try to convert a file using one of the loaded converters. Converters
        are selected based on file extension only. Raise LookupError when no
        converter could be found. Raise RuntimeError when error parsing file.

        Once file is converted, HTML content is cached. Next time content can
        be retrieved straight from cache and reducing processing time, unless
        input file has been modified in which case it will be regenerated and
        cached again.
        """

        # Pick converter based on filename extension
        filename = self.request.path.lstrip("/")
        _, extension = os.path.splitext(filename)
        filetype = extension.strip(".").lower()

        # Pass through files without converter
        if filetype not in self.converters:
            self.getStaticFile(filename)
            return

        # Get last modified time but also determine whether file exists.
        # Otherwise quit with 404 error. All other exceptions in this function
        # turn into 500 Internal error.
        try:
            fileTime = self.loader.getModifiedTime(filename)
        except WebEpicsWarning, e:
            if e.error:
                raise tornado.web.HTTPError(status_code=e.error)
            raise

        # Hopefully we've generated the file in the past that we can reuse
        if self.cache and fileTime <= self.cache.getModifiedTime(filename):
            html = self.cache.read(filename)
        else:
            # Load original file and convert it using selected converter
            orig = self.loader.get(filename)
            try:
                html = self.converters[filetype].getHtml(orig) # Don't pass run-time macros
            except Exception, e:
                log.error(str(e))
                raise tornado.web.HTTPError(status_code=500)

            # Save to cache
            if self.cache:
                self.cache.save(filename, html)

        # Replace run-time macros
        # request.arguments is a dictionary of lists in case multiple
        # names are defined in query. We only care about last macro
        # definition.
        macros = {k: v[-1] for k,v in self.request.arguments.iteritems()}
        html = self.converters[filetype].replaceMacros(html, macros)

        # We're done
        self.write(html)

    def getStaticFile(self, filename):
        """ Load a file using FileLoader without processing. """

        if self.loader.isRemote():
            raise RuntimeError("Not yet implemented")
        else:
            try:
                self.set_header("Content-Type", self.loader.getContentType(filename))
                self.write(self.loader.get(filename))
            except WebEpicsWarning, e:
                if e.error:
                    raise tornado.web.HTTPError(status_code=e.error)
                raise

    def check_etag_header(self):
        return False
