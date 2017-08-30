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

from common import WebEpicsError, WebEpicsWarning
import opi

import tornado.web
import tornado.escape

log = logging.getLogger(__name__)

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
    def createContext(cfg, wsUrlPattern):
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
        ctx["wsUrlPattern"] = wsUrlPattern

        # Setup converters
        ctx["converters"] = {}
        if "opi" in cfg:
            if "templates" not in cfg["opi"]:
                log.error("Configuration error: missing 'opi/templates' directive")
            else:
                ctx["converters"]["opi"] = opi.Converter(cfg["opi"]["templates"], ctx["cache"] != None)
                log.info("Loaded .opi file converter")

        return ctx

    def initialize(self, ctx):
        """ Called by Tornado before every request, the only place to pass in ctx. """
        self.loader = ctx["loader"]
        self.cache = ctx["cache"]
        self.converters = ctx["converters"]
        self.wsUrlPattern = ctx["wsUrlPattern"]

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
        macros = {}
        for k,v in self.request.arguments.iteritems():
            macros[k] = v[-1]

        # Push WebSocket server URL as run-time macro
        macros["WEBSOCKET_URL"] = self.getWebSocketUrl()

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

    def getWebSocketUrl(self):
        """ Returns full URL for the WebSocket connection.

        This works regardless of multi-IP servers, since the request has
        already been established so use its IP/hostname.
        """
        hostname = urlparse.urlparse(self.wsUrlPattern).netloc
        url = self.wsUrlPattern.replace(hostname, self.request.host)
        return url
