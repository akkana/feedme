#!/usr/bin/env python3

# feedme: download RSS/Atom feeds and convert to HTML, epub, Plucker,
# or other formats suitable for offline reading on a handheld device,
#
# Copyright 2009-2017 by Akkana Peck <akkana@shallowsky.com>
# and licensed under the GPLv2 or later.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details:
# <http://www.gnu.org/licenses/>.

from __future__ import print_function

ConfigHelp = """Configuration options:

Configuration options most useful in a DEFAULT section,
applicable to all feeds:
  ascii
    Convert all pages to plain ASCII. Useful for reading devices like Palm
    that can't display other character sets reliably.
  block_nonlocal_images
    If images aren't downloaded, normally the img tag will still point
    to the image on the original website. But that can result in unwanted
    data use: people with limited or metered bandwidth can set this to True
    to remove the original image URL so it can't be downloaded when the
    story is viewed.
  dir
    Where to save the collected pages.
    See save_days for how long they will be kept.
  formats
    Comma-separated list of output formats.
    Default "none", which will result in HTML output.
    Other options: epub, fb2, plucker.
  logfile
    Save output (including debugging) to this log.
  verbose
    Print lots of debugging chatter while feeding.
  min_width
    The minimum number of characters in an item link. Links shorter than this
    will be padded to this length (to make tapping easier). Default 25.
  save_days
    How long to retain feeds locally.

Configuration options you might want to reset for specific feeds:
  continue_on_timeout
    Normally, if one page times out, feedme will assume the site is down.
    On sites that link to content from many different URLs, set this
    to true.
  encoding
    Normally feedme will try to guess the encoding from the page.
    But some pages lie, so use this to override that.
  levels
    Level 1: only save the RSS page.
    Level 2: save sub-pages.
  nocache
    Don't check whether we've seen an entry before: collect everything.
  nonlocal_images
    Normally feedme will ignore images from other domains (usually ads).
    But some sites link to images from all over; set this to true in that case.
  page_start, page_end
    regexps that define the part of a page that will be fetched.
  skip_images
    Don't save images. Default true.
  skip_links:
    For sites with levels=1 where you just want a single news feed and
    never want to click on anything (e.g. slashdot), this can eliminate
    distracting links that you might tap on accidentally while scrolling.
  skip_pats
    Throw out anything matching these patterns
  url
    The RSS URL for the site.
  when
    When to check this site, if not every time.
    May be a weekday, e.g. Sat, or a month date, e.g. 1 to check only
    on the first day of any month.
"""

import time
import os, sys
import re
#import types
import shutil
import traceback

import feedparser
import urllib.error
import socket
import posixpath

# sheesh, this is apparently the recommended way to parse RFC 2822 dates:
import email.utils as email_utils

# FeedMe's module for parsing HTML inside feeds:
import feedmeparser

# Allow links in top page content
feedparser._HTMLSanitizer.acceptable_elements.add('a')
feedparser._HTMLSanitizer.acceptable_elements.add('img')

from bs4 import BeautifulSoup

# Use XDG for the config and cache directories if it's available
try:
    import xdg.BaseDirectory
except:
    pass

# For importing helper modules
import importlib
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                             "helpers"))

def expanduser(name):
    """Do what os.path.expanduser does, but also allow $HOME in paths"""
    # config.get alas doesn't substitute $HOME or ~
    if name[0:2] == "~/":
        name = os.path.join(os.environ['HOME'], name[2:])
    elif name[0:6] == "$HOME/":
        name = os.path.join(os.environ['HOME'], name[6:])
    return name

#
# Clean up old feed directories
#
def clean_up(config):
    try:
        days = int(config.get('DEFAULT', 'save_days'))
        feedsdir = expanduser(config.get('DEFAULT', 'dir'))
        cachedir = FeedmeCache.get_cache_dir()
    except:
        print("Error trying to get save_days and feed dir; can't clean up", file=sys.stderr)
        return

    now = time.time()

    def clean_up_dir(dirname, rmdir):
        '''If rmdir==True, remove (recursively) old directories,
           ignoring files at the toplevel.
           Otherwise remove old files at the toplevel.
        '''
        for f in os.listdir(dirname):
            f = os.path.join(dirname, f)

            # Logical xor: if rmdir is set, it's not a directory,
            # but not both, then skip this entry.
            # ^ is bitwise xor but works if both args are bool.
            if rmdir ^ os.path.isdir(f):
                continue

            try:
                howold = (now - os.path.getctime(f)) / 60 / 60 / 24
                if howold > days:
                    print("Deleting", f, file=sys.stderr)
                    if os.path.isdir(f):
                        shutil.rmtree(f)
                    else:
                        os.unlink(f)
            except Exception as e:
                print("Couldn't unlink", f, str(e))

    print("Cleaning up files older than %d days from feed and cache dirs" % days)
    clean_up_dir(feedsdir, True)
    clean_up_dir(cachedir, False)

##################################################################
# OUTPUT GENERATING FUNCTIONS
# Define functions for each output format you need to support.
#

def run_conversion_cmd(appargs):
    if True or verbose:
        cmd = " ".join(appargs)
        print("Running:", cmd, file=sys.stderr)
        sys.stdout.flush()

    retval = os.spawnvp(os.P_WAIT, appargs[0], appargs)
    #retval = os.system(cmd)
    if retval != 0:
        raise OSError(retval, "Couldn't run: " + ' '.join(appargs))

#
# Generate a Plucker file
#
def make_plucker_file(indexfile, feedname, levels, ascii):
    day = time.strftime("%a")
    docname = day + " " + feedname
    cleanfilename = day + "_" + feedname.replace(" ", "_")

    # Make sure the plucker directory exists:
    pluckerdir = os.path.join(expanduser("~/.plucker"), "feedme")
    if not os.path.exists(pluckerdir):
        os.makedirs(pluckerdir)

    # Run plucker. This should eventually be configurable --
    # but how, with arguments like these?

    # Plucker mysteriously creates unbeamable files if the
    # document name has a colons in it.
    # So use the less pretty but safer underscored docname.
    #docname = cleanfilename
    appargs = [ "plucker-build", "-N", docname,
                "-f", os.path.join("feedme", cleanfilename),
                "--stayonhost", "--noimages",
                "--maxdepth", str(levels),
                "--zlib-compression", "--beamable",
                "-H", "file://" + indexfile ]
    if not ascii:
        appargs.append("--charset=utf-8")

    run_conversion_cmd(appargs)

#
# http://calibre-ebook.com/user_manual/conversion.html
#
def make_calibre_file(indexfile, feedname, extension, levels, ascii,
                      author, flags):
    day = time.strftime("%a")
    # Prepend daynum to the filename because fbreader can only sort by filename
    #daynum = time.strftime("%w")
    cleanfilename = day + "_" + feedname.replace(" ", "_")
    outdir = os.path.join(config.get('DEFAULT', 'dir'), extension[1:])
    if not os.access(outdir, os.W_OK):
        os.makedirs(outdir)

    appargs = [ "ebook-convert",
                indexfile,
                #os.path.join(expanduser("~/feeds"),
                #             cleanfilename + extension),
                # directory should be configurable too, probably
                os.path.join(outdir, cleanfilename + extension),
                "--authors", author ]
    for flag in flags:
        appargs.append(flag)
    if True or verbose:
        cmd = " ".join(appargs)
        print("Running:", cmd, file=sys.stderr)
        sys.stdout.flush()

    run_conversion_cmd(appargs)

#
# Generate a fictionbook2 file
#
def make_fb2_file(indexfile, feedname, levels, ascii):
    make_calibre_file(indexfile, feedname, ".fb2", levels, ascii,
                      "feedme", flags = [ "--disable-font-rescaling" ] )

#
# Generate an ePub file
# http://calibre-ebook.com/user_manual/cli/ebook-convert-3.html#html-input-to-epub-output
# XXX Would be nice to have a way to do this without needing calibre,
# so it could run on servers that don't have X/Qt libraries installed.
#
def make_epub_file(indexfile, feedname, levels, ascii):
    make_calibre_file(indexfile, feedname, ".epub", levels, ascii,
                      time.strftime("%m-%d %a") + " feeds",
                      flags = [ '--no-default-epub-cover',
                                '--dont-split-on-page-breaks' ])

# END OUTPUT GENERATING FUNCTIONS
##################################################################

##################################################################
# MsgLog: Print messages and also batch them up to print at the end:
#
class MsgLog:
    def __init__(self):
        self.msgstr = ""
        self.errstr = ""

    def msg(self, s):
        self.msgstr += "\n" + s
        print("MESSAGE:", s, file=sys.stderr)

    def warn(self, s):
        self.msgstr += "\n" + s
        print("WARNING:", s, file=sys.stderr)

    def err(self, s):
        self.errstr += "\n" + s
        print("ERROR:", s, file=sys.stderr)

    def get_msgs(self):
        return self.msgstr

    def get_errs(self):
        return self.errstr

class tee():
    '''A file-like class that can optionally send output to a log file.
       Inspired by
http://www.redmountainsw.com/wordpress/archives/python-subclassing-file-types
       and with IRC help from Kirk McDonald.
    '''
    def __init__(self, _fd1, _fd2):
        self.fd1 = _fd1
        self.fd2 = _fd2

    def __del__(self):
        if self.fd1 != sys.stdout and self.fd1 != sys.stderr:
            self.fd1.close()
        if self.fd2 != sys.stdout and self.fd2 != sys.stderr:
            self.fd2.close()

    def write(self, text):
        self.fd1.write(text)
        # UnicodeEncodeError: 'ascii' codec can't encode character '\u2019' in position 4: ordinal not in range(128)
        # fd1 is stderr.
        # fd2 was opened with: outputlog = open(logfilename, "w", buffering=1)
        # But it only happens when invoked from the web server,
        # maybe because the web server's environment is C rather than UTF-8,
        # and seemingly only when initiated from a phone (not from wget).
        try:
            self.fd2.write(text)
        except UnicodeEncodeError:
            s = "caught a UnicodeEncodeError trying to write a " \
                + str(type(text)) + '\n'
            self.fd1.write(s)
            self.fd2.write(s)
            # This just raises another error, probably for the same reason:
            # feedmeparser.ptraceback()

    def flush(self):
        self.fd1.flush()
        self.fd2.flush()

#
# Ctrl-C Interrupt handler: prompt for what to do.
#
def handle_keyboard_interrupt(msg):
    # os.isatty() doesn't work, so:
    if not hasattr(sys.stdin, "isatty"):
        print("Interrupt, and not running interactively. Exiting.")
        sys.exit(1)

    try:
        response = input(msg)
    except EOFError:
        # This happens if we're run from a script rather than interactively
        # and yet someone sends a SIGINT, perhaps because we're timing out
        # and someone logged in to kick us back into operation.
        # In this case, pretend the user typed 'n',
        # meaning skip to next site.
        return 'n'
    if response == '':
        return '\0'
    if response[0] == 'q':
        sys.exit(1)
    return response[0]

def falls_between(when, time1, time2):
    """Does a given day-of-week or day-of-month fall between
       the two given times? It is presumed that time1 <= time2.
       If when == "Tue", did we cross a tuesday getting from time1 to time2?
       If when == 15, did we cross the 15th of a month?
       If when == none, return True.
       If when matches time2, return True.
    """
    if not when or type(when) is str and len(when) <= 0:
        return True

    # We need both times both in seconds since epoch and in struct_time:
    def both_time_types(t):
        """Given a time that might be either seconds since epoch or struct_time,
           return a tuple of (seconds, struct_time).
        """
        if type(t) is time.struct_time:
            return time.mktime(t), t
        elif type(t) is int or type(t) is float:
            return t, time.localtime(t)
        else : raise ValueError("%s not int or struct_time" % str(t))

    (t1, st1) = both_time_types(time1)
    (t2, st2) = both_time_types(time2)

    daysdiff = (t2 - t1) / 60. / 60. / 24.
    if daysdiff < 0:
        msglog.err("daysdiff < 0!!! " + str(daysdiff))

    # Is it a day of the month?
    try:
        day_of_month = int(when)

        # It is a day of the month! How many days in between the two dates?
        if daysdiff > 31:
            return True

        # Now we know the two dates differ by less than a month.
        # Are time1 and time2 both in the same month? Then it's easy.
        if st1.tm_mon == st2.tm_mon:
            return st1.tm_mday <= day_of_month and st2.tm_mday >= day_of_month

        # Else time1 is the month prior to time2, so:
        return st1.tm_mday < day_of_month or day_of_month <= st2.tm_mday

    except ValueError :  # Not an integer, probably a string.
        pass

    if type(when) is not str:
        raise ValueError("%s must be a string or integer" % when)

    # Okay, not a day of the month. Is it a day of the week?
    # We have to start with Monday because struct_time.tm_wday does.
    weekdays = [ 'mo', 'tu', 'we', 'th', 'fr', 'sa', 'su' ]
    if len(when) < 2:
        raise ValueError("%s too short: days must have at least 2 chars" % when)

    when = when[0:2].lower()
    if when not in weekdays:
        raise ValueError("%s is a string but not a day" % when)

    # Whew -- we know it's a day of the week.

    # Has more than a week passed? Then it encompasses all weekdays.
    if daysdiff > 7:
        return True

    day_of_week = weekdays.index(when)
    return (st2.tm_wday - day_of_week) % 7 < daysdiff

def last_time_this_feed(feeddir):
    '''Return the last time we fetched a given feed.
       This is most useful for feeds that randomly show old entries.
       Pass in the intended outdir, e.g. .../feeds/08-11-Thu/feedname
       Returns seconds since epoch.
    '''
    feeddir, feedname = os.path.split(feeddir)
    feeddir = os.path.dirname(feeddir)

    if not os.path.exists(feeddir):
        return 0

    newest_mtime = 0
    newest_mtime_dir = None
    newest_parsed_time = 0
    newest_parsed_dir = None

    # Now feeddir is the top level feeds directory, containing dated subdirs.
    # Look over old feed subdirs to find the most recent time we fed
    # this particular feedname.
    for d in os.listdir(feeddir):
        dpath = os.path.join(feeddir, d)
        if os.path.isdir(dpath):
            oldfeeddir = os.path.join(dpath, feedname)
            if os.path.isdir(oldfeeddir):
                # We could do this one of two ways.
                # d has a name like "08-03-Wed", so we could parse that.
                # Or we could use the last modified date of the directory.
                # Use both, and compare them.
                # These are both seconds since epoch.
                modtime = os.stat(oldfeeddir).st_mtime
                if modtime > newest_mtime:
                    newest_mtime = modtime
                    newest_mtime_dir = d

                # As of October 2016 suddenly strptime has a new error mode
                # where it can get a ValueError: unconverted data remains.
                # Guard against this:
                try:
                    # The feed directory name doesn't have a year,
                    # so make the year the same as the modtime:
                    ddate = "%s-%d" % (d, time.localtime(modtime).tm_year)
                    parsed_time = time.mktime(time.strptime(ddate,
                                                            "%m-%d-%a-%Y"))
                except ValueError:
                    msglog.msg("Skipping directory %s" % d)
                    continue
                if parsed_time > newest_parsed_time:
                    newest_parsed_time = parsed_time
                    newest_parsed_dir = d

    if newest_mtime_dir != newest_parsed_dir:
        msglog.warn("Last time we fetched %s was %s, but dir was %s" \
                    % (feedname,
                       time.strftime("%a-%Y-%m-%d",
                                     time.localtime(newest_mtime)),
                       newest_parsed_dir))
    # else:
    #     # XXX This should only print if verbose,
    #     # but this function doesn't know whether we're verbose.
    #     print >>sys.stderr, "Last time we fetched %s was %s" \
    #         % (feedname, newest_parsed_dir))

    return newest_mtime

class FeedmeCache(object):
    '''The FeedmeCache is a dictionary where the keys are site RSS URLs,
       and for each feed we have a list of URLs we've seen.
       { siteurl: [ url, url, url, ...] }
       It's best to create a new FeedmeCache using the static method
       FeedmeCache.newcache().
       filename is the cache file we're using;
       last_time is the last modified time of the cache file, or None.
    '''
    def __init__(self, cachefile):
        self.filename = cachefile
        self.thedict = {}
        self.last_time = None

    @staticmethod
    def get_cache_dir():
        if 'XDG_CACHE_HOME' in os.environ:
            cachehome = os.environ['XDG_CACHE_HOME']
        elif 'xdg.BaseDirectory' in sys.modules:
            cachehome = xdg.BaseDirectory.xdg_cache_home
        else:
            cachehome = expanduser('~/.cache')

        return os.path.join(cachehome, 'feedme')

    @staticmethod
    def newcache():
        '''Find the cache file and load it into a newly created Cache object,
           returning the cache object.
           If there's no cache file yet, create one.
        '''
        cachefile = os.path.join(FeedmeCache.get_cache_dir(), "feedme.dat")

        if not os.access(cachefile, os.W_OK):
            dirname = os.path.dirname(cachefile)
            if not os.path.exists(dirname):
                os.makedirs(dirname)
            cache = FeedmeCache(cachefile)
            cache.last_time = None

        else:
            cache = FeedmeCache(cachefile)

            # Make a backup of the cache file, in case something goes wrong.
            cache.back_up()
            cache.last_time = os.stat(cachefile).st_mtime
            cache.read_from_file()

        return cache

    #
    # New style cache files are human readable and look like this:
    # FeedMe v. 1
    # siteurl|time|url url, url ...
    # One line per site.
    # urls are a list of URLs on the RSS feed the last time we looked.
    # Time is the last time we updated this site, seconds since epoch.
    # Urls must all be urlencoded,
    # and in particular must have no spaces or colons.
    #
    def read_from_file(self):
        '''Read cache from a cache file, either old or new style.'''
        with open(self.filename) as fp:
            contents = fp.read()

        if not contents.startswith("FeedMe v."):
            print("Sorry, old-style pickle-based cache files are "
                  "no longer supported.\nStarting over without cache.")
            # It's an old style, pickle-based file.
            return

        # Must be a new-style file.
        for line in contents.split('\n')[1:]:
            if not line.strip():
                continue
            try:
                key, urllist = line.split('|')
            except ValueError:
                print("Problem splitting on |:", line, file=sys.stderr)
                continue
            key = key.strip()
            urls = urllist.strip().split()

            self.thedict[key] = urls

    def back_up(self):
        '''Back up the cache file to a file named for when
           the last cache, self.filename, was last modified.
        '''
        try:
            mtime = os.stat(self.filename).st_mtime
            timeappend = time.strftime("%y-%m-%d-%a", time.localtime(mtime))

            base, ext = os.path.splitext(self.filename)
            backupfilebase = "%s-%s%s" % (base, timeappend, ext)
            num = 0
            for num in range(10):
                if num:
                    backupfile = "%s-%d" % (backupfilebase, num)
                else:
                    backupfile = backupfilebase
                if not os.path.exists(backupfile):
                    break
            print("Backing up cache file to", backupfile)
            shutil.copy2(self.filename, backupfile)
        except Exception as e:
            msglog.warn("WARNING: Couldn't back up cache file!")
            print(str(e), file=sys.stderr)
            feedmeparser.ptraceback()

    def save_to_file(self):
        '''Serialize the cache to a version-1 new style cache file.
           The file should already have been backed up by newcache().
        '''
        # Write the new cache file.
        with open(self.filename, "w") as fp:
            print("FeedMe v. 1", file=fp)
            for k in self.thedict:
                print("%s|%s" % (FeedmeCache.id_encode(k),
                                 ' '.join(map(FeedmeCache.id_encode,
                                              self.thedict[k]))), file=fp)

        # Remove backups older than N days.
        # XXX should pass in save_days from config file
        cachedir = os.path.dirname(self.filename)
        files = os.listdir(cachedir)
        for f in files:
            if not f.startswith("feedme."):
                continue
            # does it have six numbers after the feedme?
            try:
                d = int(f[7:14])
            except ValueError:
                continue
            # It matches feedme.nnnnnn. How old is it? st_mtime is secs.
            mtime = os.stat(f).st_mtime
            age_days = (time.time() - mtime) / 60 / 60 / 24
            if age_days > 5:
                print("Removing old cache", f, file=sys.stderr)
                os.unlink(f)

    def save_to_file_pickle(self):
        '''Serialize the cache to an old-style pickle cachefile.'''
        t = time.time()
        cPickle.dump(cache, open(self.filename, 'w'))
        print("Writing cache took", time.time() - t, "seconds", file=sys.stderr)

    def __repr__(self):
        return self.thedict.__repr__()

    @staticmethod
    def id_encode(s):
        return s.replace(' ', '+')

    # Dictionary class forwarded methods:
    def __getitem__(self, key):
        return self.thedict.__getitem__(key)

    def __setitem__(self, key, val):
        return self.thedict.__setitem__(key, val)

    def __delitem__(self, name):
        return self.thedict.__delitem__(name)

    def __len__(self):
        # Dictionaries don't always/reliably have __len__, apparently;
        # just calling self.__len__() sometimes fails with
        # TypeError: an integer is required
        return len(list(self.thedict.keys()))

    def __iter__(self):
        return self.thedict.__iter__()

    def __contains__(self, item):
        return self.thedict.__contains__(item)

    def keys(self):
        return list(self.thedict.keys())

def parse_name_from_conf_file(feedfile):
    """Given the full pathname to a .conf file name,
       return the site name from the initial [The Site Name] line.
    """
    with open(feedfile) as fp:
        for line in fp:
            m = re.match('^\b*\[(.*)\]\b*$', line)
            if m:
                return m.group(1)
    return None

#
# Get a single feed
#
def get_feed(feedname, config, cache, last_time, msglog):
    """Fetch a single site's feed.
       feedname can be the feed's config name ("Washington Post")
       or the conf file name ("washingtonpost" or "washingtonpost.conf").
    """
    verbose = (config.get("DEFAULT", 'verbose').lower() == 'true')

    # Mandatory arguments:
    try:
        sitefeedurl = config.get(feedname, 'url')
        feedsdir = config.get(feedname, 'dir')
    except Exception as e:
        sitefeedurl = None

    # If feedname isn't a name in the config files, maybe it's the name
    # of a config file itself, e.g. if not "Foo News",
    # then maybe foonews or foonews.conf.
    if not sitefeedurl:
        fakefeedname = None
        if os.path.exists(feedname):
            # XXX This clause will accept the full path to a .conf file as
            # a commandline argument -- but that file will only be
            # used for the feed name, not for the actual feed parameters
            # or other config values, which probably isn't what the user
            # expects. The config object has already been initialized
            # by this time, and overwriting it is probably more work than
            # is warranted given that I never actually expect to use
            # config files from outside the configdir.
            fakefeedname = parse_name_from_conf_file(feedname)
            if fakefeedname:
                msglog.warn("Warning: Using name '%s' from %s,"
                            " but config parameters will actually be parsed "
                            " from files in %s"
                            % (fakefeedname, feedname,
                               feedmeparser.default_confdir))
        else:
            feedfile = os.path.join(feedmeparser.default_confdir, feedname)
            if os.path.exists(feedfile):
                fakefeedname = parse_name_from_conf_file(feedfile)
            if not sitefeedurl and not feedfile.endswith(".conf"):
                feedfile += ".conf"
                if os.path.exists(feedfile):
                    fakefeedname = parse_name_from_conf_file(feedfile)

        if fakefeedname:
            try:
                sitefeedurl = config.get(fakefeedname, 'url')
                feedsdir = config.get(fakefeedname, 'dir')
                feedname = fakefeedname
            except:
                if verbose:
                    print(feedname, "isn't a site feed name either",
                          file=sys.stderr)

    if verbose:
        print("\n=============\nGetting %s feed" % feedname, file=sys.stderr)

    if not sitefeedurl:
        msglog.err("Can't find a config for: " + feedname)
        return

    verbose = (config.get(feedname, 'verbose').lower() == 'true')
    levels = int(config.get(feedname, 'levels'))

    feedsdir = expanduser(feedsdir)
    todaystr = time.strftime("%m-%d-%a")
    feedsdir = os.path.join(feedsdir, todaystr)

    formats = config.get(feedname, 'formats').split(',')
    encoding = config.get(feedname, 'encoding')
    ascii = config.getboolean(feedname, 'ascii')
    skip_links = config.getboolean(feedname, 'skip_links')
    skip_link_pats = feedmeparser.get_config_multiline(config, feedname,
                                                      'skip_link_pats')
    skip_title_pats = feedmeparser.get_config_multiline(config, feedname,
                                                        'skip_title_pats')

    user_agent = config.get(feedname, 'user_agent')

    # Is this a feed we should only check occasionally?
    """Does this feed specify only gathering at certain times?
       If so, has such a time passed since the last time the
       cache file was written?
    """
    when = config.get(feedname, "when")
    if when and when != '' and last_time:
        if not falls_between(when, last_time, time.localtime()):
            print("Skipping", feedname, "-- not", when, file=sys.stderr)
            return
        print("Yes, it's time to feed:", when, file=sys.stderr)

    #encoding = config.get(feedname, 'encoding')

    print("\n============\nfeedname:", feedname, file=sys.stderr)
    # Use underscores rather than spaces in the filename.
    feednamedir = feedname.replace(" ", "_")
    # Also, make sure there are no colons (illegal in filenames):
    feednamedir = feednamedir.replace(":", "")
    outdir = os.path.join(feedsdir,  feednamedir)
    if verbose:
        print("feednamedir:", feednamedir, file=sys.stderr)
        print("outdir:", outdir, file=sys.stderr)

    # Get any helpers for this feed, if any.
    # A feed_helper takes precedence over a page_helper.
    # The helpers subdir has already been added to os.path,
    # at the end, so if the user has an earlier version
    # it will override a built-in of the same name.
    try:
        feed_helper = config.get(feedname, 'feed_helper')
    except:
        feed_helper = None
        try:
            page_helper = config.get(feedname, 'page_helper')
        except:
            page_helper = None

    if feed_helper or page_helper:
        # Read all helper args, which start with "helper_",
        # $D will map to today's datedir.
        # No tilde expansion will be done.
        # Turn them into a dictionary, e.g.
        #     helper_executable_path = ~/firefox-esr
        #     helper_log = $d/nyt_selenium.log
        # -> {
        #      "executable_path": "~/firefox-esr",
        #      "log": "/home/username/feeds/10-25-Mon/nyt_selenium.log"
        #    }
        confoptions = config.options(feedname)
        helper_args = {}
        for opt in confoptions:
            if opt.startswith("helper_"):
                key = opt[7:]
                if key:
                    helper_args[key] = config.get(feedname, opt)
                    if '$f' in helper_args[key] and \
                       r'\$f' not in helper_args[key]:
                        helper_args[key] = helper_args[key].replace("$f",
                                                                    outdir)
                    if '$d' in helper_args[key] and \
                       r'\$d' not in helper_args[key]:
                        helper_args[key] = helper_args[key].replace("$d",
                                                                    feedsdir)
                else:
                    print("Skipping bad key '%s' in %s config file"
                          % (opt, feedname), file-sys.stderr)
        if verbose:
            print(feedname, "helper args:", helper_args, file=sys.stderr)

        if feed_helper:
            if verbose:
                print("Trying to import", feed_helper)
            try:
                helpermod = importlib.import_module(feed_helper)
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                print("Couldn't import module '%s'" % feed_helper,
                      file=sys.stderr)

            try:
                helpermod.fetch_feed(outdir, helper_args)

                if verbose:
                    print("Fetched feed with %s(%s) to %s"
                          % (feed_helper, helper_args, outdir))
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                print("Couldn't run helper module '%s'" % feed_helper,
                      file=sys.stderr)

            # Whether the copy helper was successful or not,
            # it's time to return.
            return

        else:    # must be a page_helper
            if verbose:
                print("Trying to import", page_helper)
            try:
                helpermod = importlib.import_module(page_helper)
                if verbose:
                    print("Initializing", page_helper, file=sys.stderr)
                helpermod.initialize(helper_args)
            except Exception as e:
                print("Couldn't import module '%s'" % page_helper,
                      file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                return

    else:
        helpermod = None

    # When did we last run this feed?
    # This code is probably brittle so wrap it in try/except.
    last_fed_this = last_time_this_feed(outdir)
    if verbose:
        print("Last fetched %s on %s" % (feedname, str(last_fed_this)),
              file=sys.stderr)

    if cache == None:
        nocache = True
    else:
        nocache = (config.get(feedname, 'nocache') == 'true')
    if verbose and nocache:
        msglog.msg(feedname + ": Ignoring cache")

    downloaded_string ="\n<hr><i>(Downloaded by " + \
        feedmeparser.VersionString + ")</i>\n"

    # feedparser doesn't understand file:// URLs, so translate those
    # to a local file:
    # Ironically, with newer changes to feedparser, now file://
    # is the only type it *does* handle reliably, and anything else
    # we have to fetch for it before it can parse.
    # if sitefeedurl.startswith('file://'):
    #     sitefeedurl = sitefeedurl[7:]

    # feedparser.parse() can throw unexplained errors like
    # "xml.sax._exceptions.SAXException: Read failed (no details available)"
    # which will kill our whole process, so guard against that.
    # Sadly, feedparser usually doesn't give any details about what went wrong.
    socket.setdefaulttimeout(100)
    try:
        print("Running: feedparser.parse(%s)" % (sitefeedurl), file=sys.stderr)

        # Feedparser sometimes makes bogus decisions about charsets
        # fetched from http servers.
        # For instance, on http://www.lamonitor.com/todaysnews/rss.xml
        # some versions of feedparser (actually, some version of some
        # underlying library it uses, but since feedparser's documentation
        # is so sketchy and it's so inflexible, it's impossible to tell
        # exactly where the problem is) will ignore the encoding specified
        # in the feed and randomly decide to use something else.
        # For instance, http://www.lamonitor.com/todaysnews/rss.xml
        # specifies encoding="utf-8", but on Debian Jessie the parsed feed
        # has 'encoding': u'iso-8859-2'.
        # (Debian Stretch gets the right answer of utf-8. Even though
        # the versions of feedfetcher, chardet and urllib2 are identical.)
        # But if we fetch the RSS explicitly with urllib2 and pass it
        # as a string to feedfetcher.parse(), it doesn't do this.
        # feed = feedparser.parse(sitefeedurl)
        # HOWEVER:
        # If we do this on file:// URLs it causes an "unknown url type"
        # error -- no idea why. I just love feedparser so much. :-(
        if sitefeedurl.startswith("file://"):
            feed = feedparser.parse(sitefeedurl)
        else:
            downloader = feedmeparser.FeedmeURLDownloader(config, feedname)
            rss_str = downloader.download_url(sitefeedurl)
            feed = feedparser.parse(rss_str)
            rss_str = None
            response = None

    # except xml.sax._exceptions.SAXException, e:
    except urllib.error.HTTPError as e:
        print("HTTP error parsing URL:", sitefeedurl, file=sys.stderr)
        print(str(e), file=sys.stderr)
        return

    except feedmeparser.CookieError as e:
        msglog.err("No cookies, skipping site")
        msglog.err("Error was: %s" % e.message)
        msglog.err("Cookiefile details: %s\n" % e.longmessage)
        return

    except Exception as e:
        print("Couldn't parse feed: URL:", sitefeedurl, file=sys.stderr)
        print(str(e), file=sys.stderr)
        # raise(e)
        feedmeparser.ptraceback()
        # print(traceback.format_exc())
        return

    # feedparser has no error return! One way is to check len(feed.feed).
    if len(feed.feed) == 0:
        msglog.err("Can't read " + sitefeedurl)
        return

    # XXX Sometimes feeds die a few lines later getting feed.feed.title.
    # Here's a braindead guard against it -- but why isn't this
    # whole clause inside a try? It should be.
    if not 'title' in feed.feed:
        msglog.msg(sitefeedurl + " lacks a title!")
        feed.feed.title = '[' + feedname + ']'
        #return

    if not nocache:
        if sitefeedurl not in cache:
            cache[sitefeedurl] = []
        feedcache = cache[sitefeedurl]
    newfeedcache = []

    # suburls: mapping of URLs we've encountered to local URLs.
    # Any anchors (#anchor) will be discarded.
    # This is for sites like WorldWideWords that make many links
    # to the same page.
    suburls = []

    # Some sites, like Washington Post, repeat the same story
    # several times but with different URLs (and no ID specified).
    # The only way to tell we've seen them before is by title.
    titles = []

    # indexstr is the contents of the index.html file.
    # Kept as a string until we know whether there are new, non-cached
    # stories so it's worth updating the copy on disk.
    # The stylesheet is for FeedViewer and shouldn't bother plucker etc.
    day = time.strftime("%a")
    indexstr = """<html>\n<head>
<meta http-equiv=\"Content-Type\" content=\"text/html; charset=utf-8\">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s: %s</title>
<link rel="stylesheet" type="text/css" title="Feeds" href="../../feeds.css"/>
</head>

<body>\n<h1>%s: %s: %s</h1>
\n""" % (day, feedname, day, feedname, feed.feed.title)

    if verbose:
        print("********* Reading", sitefeedurl, file=sys.stderr)

    # A pattern to tell the user how to get to the next story: >->
    # We also might want to remove that pattern later, in case
    # a story wasn't successfully downloaded -- so make a
    # regexp that can match it.
    next_item_string =  '<br>\n<center><i><a href=\"#%d\">&gt;-&gt;</a></i></center>\n<br>\n'
    next_item_pattern = '<br>\n<center><i><a href=\"#[0-9]+\">&gt;-&gt;</a></i></center>\n<br>\n'

    # We'll increment itemnum as soon as we start showing entries,
    # so start it negative so anchor links will start at zero.
    itemnum = -1
    last_page_written = None
    for item in feed.entries:
        try:
            #
            # Get the list of links (href) and a (hopefully) unique ID.
            # Make sure the id is a string; most of these components
            # inside item are bytes.
            # XXX Is href[] ever even used? Is this clause obsolete?
            # Answer: not obsolete, at least A Word A Day uses it.
            # But sometimes this clause will be triggered on a site
            # that doesn't have "links" in its RSS source
            # (e.g. Washington Post), which then won't have href either.
            #
            if 'links' in item:
                href = [str(i['href']) for i in item.links
                        if 'rel' in i and 'href' in i
                        and i['rel'] == 'alternate']
            else:
                href = []

            if 'id' in item:
                item_id = str(item.id)
                if verbose:
                    print("\nID %s" % item_id, file=sys.stderr)
            elif href:
                item_id = str(href[0])
                if verbose:
                    print("Using URL '%s' for ID" % item_id, file=sys.stderr)
            else:
                if verbose:
                    print("Item in %s had no ID or URL." % str(href[0]),
                          file=sys.stderr)
                    continue  # or return?

            # Whatever pattern we're using for the ID, it will need to
            # have spaces mapped to + before putting it in the cache.
            # So do that now.
            item_id = FeedmeCache.id_encode(item_id)

            # Does the link match a pattern we're skipping?
            item_link = str(item.link)
            if skip_link_pats:
                skipping = False
                for spat in skip_link_pats:
                    if re.search(spat, item_link):
                        skipping = True
                        if verbose:
                            print("Skipping", item_link, \
                                "because it matches", spat, file=sys.stderr)
                        break
                if skipping:
                    continue

            # How about the title? Does that match a skip pattern?
            item_title = str(item.title)
            if skip_title_pats:
                skipping = False
                for pat in skip_title_pats:
                    if re.search(pat, item_title, flags=re.IGNORECASE):
                        skipping = True
                        if verbose:
                            print("Skipping", item_link, \
                                  "because of skip_title_pats " + pat,
                                  file=sys.stderr)
                        break
                if skipping:
                    continue

            # Filter out file types known not to work
            # XXX Only mp3 for now. Obviously, make this more general.
            # Wish we could do this using the server's type rather than
            # file extension!
            if item_link.endswith("mp3"):
                print("Filtering out mp3 link", item_link, file=sys.stderr)
                continue

            # Make sure ids don't have named anchors appended:
            anchor_index = item_id.rfind('#')
            if anchor_index >= 0:
                anchor = item_id[anchor_index:]
                item_id = item_id[0:anchor_index]
            else:
                anchor = ""

            # See if we've already seen this page's ID in this run:
            try:
                pagenum = suburls.index(item_id)
                # We've already seen a link to this URL.
                # That could mean it's a link to a different named anchor
                # within the same file, or it could mean that it's just
                # a duplicate (see below).
            except ValueError:
                pagenum = None

            # Is it a duplicate story that we've already seen in this run?
            # Some sites, like Washington Post, repeat the same stories
            # multiple times on their RSS feed, but stories won't be
            # added to our real feedcache until we've succeeded in
            # fetching the whole site. So check the temporary cache.
            # On the other hand, Risks Digest has a single story and
            # a lot of RSS entries with links to #name tags in that story.
            # So in that case we should include the entry but not
            # re-fetch the story.
            if newfeedcache and item_id in newfeedcache:
                if verbose:
                    print("%s repeated -- skipping" % item_id,
                          file=sys.stderr)
                continue

            # How about the title? Have we already seen that before?
            # Washington Post runs the same story (same title) several
            # times with different URLs.
            # If we ever need to handle a feed where different stories
            # have the same title, this will have to be configurable.
            if item.title in titles:
                print('Skipping repeated title with a new ID: "%s", ID "%s"' \
                      % (item.title, item_id), file=sys.stderr)
                continue
            titles.append(item.title)

            if not pagenum:
                # Get the published date.
                # item.pubDate is a unicode string, supposed to be in format
                # Thu, 11 Aug 2016 14:46:50 GMT (or +0000)
                # email.utils.parsedate returns a tuple.
                # Pass it to time.mktime() to get seconds since epoch,
                # XXX feedparser now has published_parsed which is
                # a time.struct_time. Can we count on that and not
                # have to do parsing here?
                try:
                    pub_date = time.mktime(email_utils.parsedate(item.published))
                except:
                    pub_date = None

                # Haven't seen it yet this run. But is it in the cache already?
                if not nocache:
                    # We want it in the cache, whether it's new or not:
                    if verbose:
                        print("Will cache as %s" % item_id, file=sys.stderr)
                    newfeedcache.append(item_id)
                    if item_id in feedcache:
                        # We've seen this ID before. HOWEVER, it may still
                        # be new: a site might have a static URL for the
                        # monthly photo contest that gets updated once
                        # a month with all-new content.
                        if not config.getboolean(feedname, 'allow_repeats'):
                            if verbose:
                                print(item_id, "already cached -- skipping",
                                      file=sys.stderr)
                            continue

                        # Repeats are allowed. So check the pub date.
                        # XXX Unfortunately cache entries don't include
                        # a date, so for now, allow repeat URLs if their
                        # content was updated since the last feedme run.
                        # This will unfortunately miss sites that
                        # aren't checked every day.
                        if verbose:
                            print("Seen this before, but repeats are allowed")
                            print("Last time this feed", last_fed_this)
                            print("pub_date", pub_date)
                            if pub_date <= last_fed_this:
                                print("No new changes, skipping")
                                continue
                            print("It's changed recently, re-fetching")

                    elif verbose:
                        print("'%s' is not in the cache -- fetching" % item_id,
                              file=sys.stderr)

                # We're probably including this item. Add it to suburls.
                suburls.append(item_id)
                pagenum = len(suburls) - 1

                # Sanity check: is the pubDate newer than the last
                # time we ran feedme? A negative answer isn't
                # necessarily a reason not to get it.
                # See if it's newer or older. If it's older,
                # we've probably seen it already; give a warning.
                if pub_date and last_fed_this:
                    if verbose:
                        print("Comparing pub_date %s to last_fed_this %s"
                              % (str(pub_date), str(last_fed_this)),
                              file=sys.stderr)
                    if pub_date < last_fed_this and (verbose or not nocache):
                        # If an entry is older than the maximum age
                        # for the cache, skip it with a warning.
                        days = int(config.get('DEFAULT', 'save_days'))
                        too_old = time.time() - days * 60 * 60 * 24
                        if pub_date <= too_old and not nocache:
                            msglog.warn("%s is so old (%s) it's expired from the cache -- skipping" \
                                        % (item_id, str(item.published)))
                            # XXX Remove from suburls?
                            continue

                        # Else warn about it, but include it in the feed.
                        msglog.warn("%s is older (%s) than the last time we updated this feed (%s)" \
                                    % (item_id, str(item.published),
                                       time.strftime("%m-%d-%a-%y",
                                                time.gmtime(last_fed_this))))
                    else:
                        print("%s: last updated %s, pubDate is %s" \
                            % (item_id, str(item.published),
                               time.strftime("%m-%d-%a-%y",
                                             time.gmtime(last_fed_this))))
                elif verbose and not pub_date:
                    print(item_id, ": No pub_date!", file=sys.stderr)

            itemnum += 1
            if verbose:
                print("Item:", item_title, file=sys.stderr)

            # Now itemnum is the number of the entry on the index page;
            # pagenum is the html file of the subentry, e.g. 3.html.

            # Make the parent directory if we haven't already
            if not os.access(outdir, os.W_OK):
                if verbose:
                    print("Making", outdir, file=sys.stderr)
                os.makedirs(outdir)

            if 'author' in item:
                author = str(item.author)
            else:
                author = None

            # A parser is mostly needed for levels > 1, but even with
            # levels=1 we'll use it at the end for rewriting images
            # in the index string.
            parser = feedmeparser.FeedmeHTMLParser(config, feedname)

            #
            # If it's a normal multi-level site,
            # follow the link and make a file for it:
            #
            if levels > 1:
                try:    # Try to trap keyboard interrupts, + others
                    # For the sub-pages, we're getting HTML, not RSS.
                    # Nobody seems to have RSS pointing to RSS.
                    fnam = str(pagenum) + ".html"

                    # Add a nextitem link in the footer to the next story,
                    # Shouldn't do this for the last story;
                    # but there's no easy way to tell if this is the last story,
                    # because we don't know until we try whether the next
                    # story will actually be fetched or not.
                    footer = '<center><a href="%d.html">&gt;-%d-&gt;</a></center>' \
                             % (itemnum+1, itemnum+1)

                    # Add the page's URL to the footer:
                    footer += downloaded_string
                    footer += '\n<br>\n<a href="%s">%s</a>' % (item_link,
                                                               item_link)

                    if helpermod:
                        try:
                            htmlstr = helpermod.fetch_article(item_link)
                        except Exception as e:
                            print("Helper couldn't fetch", item_link,
                                  file=sys.stderr)
                            traceback.print_exc(file=sys.stderr)
                            continue
                        if not htmlstr:
                            if verbose:
                                print("fetch failed on", item_link,
                                      file=sys.stderr)
                            continue
                    else:
                        htmlstr = None

                    parser.fetch_url(item_link,
                                     outdir, fnam,
                                     title=item_title, author=author,
                                     footer=footer, html=htmlstr,
                                     user_agent=user_agent)
                    last_page_written = fnam

                except feedmeparser.NoContentError as e:
                    # fetch_url didn't get the page or didn't write a file.
                    # So don't increment pagenum or itemnum for the next story.
                    msglog.warn("Didn't find any content on " + item_link
                                + ": " + str(e))
                    # It is so annoying needing to repeat these
                    # lines every time! Isn't there a way I can define
                    # a subfunction that knows about this function's
                    # local variables?
                    itemnum -= 1
                    suburls.remove(item_id)

                    # Include a note in the indexstr
                    indexstr += '<p>No content for <a href="%s">%s</a>\n' \
                                % (item_link, item_title)
                    continue

                # Catch timeouts.
                # If we get a timeout on a story,
                # we should assume the whole site has gone down,
                # and skip over the rest of the site.
                # XXX Though maybe we shouldn't give up til N timeouts.
                # In Python 2.6, instead of raising socket.timeout
                # a timeout will raise urllib2.URLerror with
                # e.reason set to socket.timeout.
                except socket.timeout as e:
                    errmsg = "Socket.timeout error on title " + item_title
                    errmsg += "\nBreaking -- hopefully we'll write index.html"
                    msglog.err(errmsg)
                    if config.get(feedname, 'continue_on_timeout') == 'true':
                        continue
                    break

                # Handle timeouts in Python 2.6
                except urllib.error.URLError as e:
                    if isinstance(e.reason, socket.timeout):
                        errmsg = "URLError Socket.timeout on title "
                        errmsg += item_title
                        errmsg += "\n"
                        errmsg += "Breaking -- hopefully we'll write index.html"
                        msglog.err(errmsg)
                        indexstr += "<p>" + errmsg
                        if config.get(feedname,
                                      'continue_on_timeout') == 'true':
                            continue
                        break

                    # Some other type of URLError.
                    errmsg = 'URLError on <a href="%s">%s</a><br>\n%s<br>\n' \
                             % (item_link, item_link, str(e))
                    msglog.err(errmsg)
                    indexstr += "<p><b>" + errmsg + "</b>"
                    continue

                except KeyboardInterrupt:
                    response = handle_keyboard_interrupt("""
*** Caught keyboard interrupt reading a story! ***\n
Options:
q: Quit
c: Continue trying to read this story
s: Skip to next story
n: Skip to next site

Which (default = s): """)
                    if response[0] == 'n' :      # next site
                        # XXX We should write an index.html here
                        # with anything we've gotten so far.
                        # Ideally we'd break out of the
                        # for item in feed.entries : loop.
                        # Wonder if there's a way to do that in python?
                        # Failing that, and hoping it's the only
                        # enclosing loop:
                        print("Breaking -- hopefully we'll write an index.html")
                        break
                        #return
                    elif response[0] != 'c' :    # next story (default)
                        continue
                    # If the response was 'c', we continue and just
                    # ignore the interrupt.

                except (IOError, urllib.error.HTTPError) as e:
                    # Collect info about what went wrong:
                    errmsg = "Couldn't read " + item_link + "\n"
                    #errmsg += "Title: " + item_title
                    if verbose:
                        errmsg += str(e) + '<br>\n'
                        #errmsg += str(sys.exc_info()[0]) + '<br>\n'
                        #errmsg += str(sys.exc_info()[1]) + '<br>\n'
                        # errmsg += traceback.format_exc()

                    if verbose:
                        print("==============", file=sys.stderr)
                    msglog.err("IO or HTTP error: " + errmsg)
                    if verbose:
                        print("==============", file=sys.stderr)

                    itemnum -= 1
                    suburls.remove(item_id)

                    #raise  # so this entry won't get stored or cached

                    continue   # Move on to next story

                except ValueError as e:
                    # urllib2 is supposed to throw a urllib2.URLError for
                    # "unknown url type", but in practice it throws ValueError.
                    # See this e.g. for doubleclick ad links in the latimes
                    # that have no spec, e.g. //ad.doubleclick.net/...
                    # Unfortunately it seems to happen in other cases too,
                    # so there's no way to separate out the urllib2 ones
                    # except by string: str(sys.exc_info()[1]) starts with
                    # "unknown url type:"
                    errmsg = "ValueError on title " + item_title + "\n"
                    # print >>sys.stderr, errmsg
                    # msglog.err will print it, no need to print it again.
                    if str(sys.exc_info()[1]).startswith("unknown url type:"):
                        # Don't show stack trace for unknown URL types,
                        # since it's a known error.
                        errmsg += str(sys.exc_info()[1]) + " - couldn't load\n"
                        msglog.warn(errmsg)
                    else:
                        errmsg += "ValueError on url " + item_link + "\n"
                        ex_type, ex, tb = sys.exc_info()
                        errmsg += traceback.format_exc(tb)
                        msglog.err(errmsg)

                    itemnum -= 1
                    suburls.remove(item_id)
                    continue

                except Exception as e:
                    # An unknown error, so report it complete with traceback.
                    errmsg = "Unknown error reading " + item_link + "\n"
                    errmsg += "Title: " + item_title
                    if verbose:
                        errmsg += "\nItem summary was:\n------\n"
                        errmsg += str(item.summary) + "\n------\n"
                        errmsg += str(e) + '<br>\n'
                        # errmsg += str(sys.exc_info()[0]) + '<br>\n'
                        # errmsg += str(sys.exc_info()[1]) + '<br>\n'
                        errmsg += traceback.format_exc()

                    if verbose:
                        print("==============", file=sys.stderr)
                    msglog.err("Unknown error: " + errmsg)
                    if verbose:
                        print("==============", file=sys.stderr)

                    # Are we sure we didn't get anything?
                    # Should we decrement itemnum, etc. ?
                    continue   # Move on to next story, ensure we get index

            # Done with if levels > 1 clause

            if not 'published_parsed' in item:
                if 'updated_parsed' in item:
                    item.published_parsed = item.updated_parsed
                else:
                    item.published_parsed = time.gmtime()

            # Plucker named anchors don't work unless preceded by a <p>
     # http://www.mail-archive.com/plucker-list@rubberchicken.org/msg07314.html
            indexstr += "<p><a name=\"%d\">&nbsp;</a>" % itemnum

            # Make sure the link is at least some minimum width.
            # This is for viewers that have special areas defined on the
            # screen, e.g. areas for paging up/down or adjusting brightness.
            minwidth = config.getint(feedname, 'min_width')
            if len(item_title) < minwidth:
                item_title += '. ' * (minwidth - len(item_title)) + '__'

            if levels > 1:
                itemlink = '<a href=\"' + fnam + anchor + '\">'
                indexstr += itemlink + '<b>' + item_title + '</b></a>\n'
            else:
                # For a single-level site, don't put links over each entry.
                if skip_links:
                    itemlink = None
                    indexstr += "\n<b>" + item_title + "</b>\n"
                else:
                    itemlink = '<a href=\"' + item_link + '\">'
                    indexstr += "\n" + itemlink + item_title + "</a>\n"

            # Under the title, add a link to jump to the next entry.
            # If it's the last entry, we'll change it to "[end]" later.
            indexstr += next_item_string % (itemnum+1)

            # Add either the content or the summary.
            # Prefer content since it might have links.
            if 'content' in item:
                content = str(item.content[0].value) + "\n"
            elif 'summary_detail' in item:
                content = str(item.summary_detail.value) + "\n"
            elif 'summary' in item:
                content = str(item.summary.value) + "\n"
            else:
                content = "[No content]"

            # Sites that put too much formatting crap in the RSS:
            if config.getboolean(feedname, 'simplify_rss'):
                simp = feedmeparser.HTMLSimplifier()
                content = simp.simplify(content) + " ... "

            # There's an increasing trend to load up RSS pages with images.
            # Try to remove them, as well as any links that contain
            # only an image.
            # XXX Eventually, try to actually display them.
            # The trick is reconciling their paths with paths from
            # individual stories so we don't get multiple copies.
            if config.getboolean(feedname, 'skip_images'):
                content = re.sub('<a [^>]*href=.*> *<img .*?></a>', '', content)
                content = re.sub('<img .*?>', '', content)
            # But if we're not skipping images, then we need to rewrite
            # image URLs to the local URLs we would have created in
            # feedmeparser.parse().
            else:
                if content.strip():
                    content = parser.rewrite_images(content)

            # Try to get rid of embedded links if skip_links is true:
            if skip_links:
                content = re.sub('<a href=.*>(.*?)</a>', '\\1', content)
            # If we're keeping links, don't keep empty ones:
            else:
                content = re.sub('<a  [^>]*href=.*> *</a>', '', content)

            # Skip any text specified in index_skip_content_pats.
            # Some sites (*cough* Pro Publica *cough*) do weird things
            # like putting huge <style> sections in the RSS.
            index_skip_pats = feedmeparser.get_config_multiline(config,
                                                      feedname,
                                                      'index_skip_content_pats')
            for pat in index_skip_pats:
                content = re.sub(pat, '', content)
                author = re.sub(pat, '', author)

            # LA Daily Post has lately started putting the entire story
            # in the description, along with a lot of formatting crap
            # that tends to make the text unreadable (color or font size).
            # Try to strip all that crap:
            entrysize = int(config.get(feedname, 'rss_entry_size'))
            if entrysize:
                content = content[:entrysize]

                # This sometimes messes up the rest of the feed,
                # because it can include unclosed tags like <ul>.
                # Fix this by parsing it as its own mini HTML page,
                # then serializing, which closes all tags.
                # It does, however, add a new dependency on BeautifulSoup.
                soup = BeautifulSoup(content, "lxml")
                # Try to append an ellipsis at the end of the last
                # text element.
                try:
                    last_text = soup.find_all(string=True)[-1]
                    ltstring = str(last_text)
                    last_text.string.replace_with(ltstring + " ...")
                    print("Added an ellipsis", file=sys.stderr)
                    content = ''.join([str(c) for c in soup.body.children])
                except Exception as e:
                    # If it didn't work, just add the ellipsis after
                    # the last element. It will probably show up on a
                    # line by itself.
                    print("Problem adding ellipsis:", e, file=sys.stderr)
                    content = ''.join([str(c) for c in soup.body.children]) \
                        +  " [...]"

            indexstr += content

            if author:
                indexstr += "\n<br><i>By: " + author + "</i><br>"

            # After the content, add another link to the title,
            # in case the user wants to click through after reading
            # the content:
            sublen = 16
            if len(item_title) > sublen:
                # Truncate the title to sublen characters, and
                # temove any HTML tags, otherwise we'll likely have
                # tags like <i> that open but don't close
                short_title = re.sub('<.*?>', '', item_title[0:sublen]) \
                    + "..."

            else:
                short_title = item_title
            if itemlink:
                indexstr += "\n<br>[[" + itemlink + short_title + "</a>]]\n\n"

        # If there was an error parsing this entry, we won't save
        # a file so decrement the itemnum and loop to the next entry.
        except KeyboardInterrupt:
            sys.stderr.flush()
            response = handle_keyboard_interrupt("""
*** Caught keyboard interrupt while finishing a site! ***\n
Options:
q: Quit
c: Continue trying to finish this site
n: Skip to next site

Which (default = n): """)
            if response[0] == 'c':
                continue
            if response[0] == 'q':
                sys.exit(1)
            # Default is to skip to the next site:
            return
        except Exception as e :    # probably an HTTPError, bad URL
            itemnum -= 1
            if verbose:
                print("Skipping item", end=' ', file=sys.stderr)
                if 'link' in item:
                    print(item_link, file=sys.stderr)
                else:
                    print("item has no link! item =", item, file=sys.stderr)
                print("error was", str(e), file=sys.stderr)

                # print(str(sys.exc_info()[0]), file=sys.stderr)
                # print(str(sys.exc_info()[1]), file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)

    # Only write the index.html file if there was content:
    if itemnum >= 0:
        # If the RSS page ended with a story we didn't include,
        # either because it's old or because it had no content we could show,
        # we'll have an extra ">->" line. Try to remove it.
        # (Ugh, python re has no cleaner way of getting the last match.)
        m = None
        for m in re.finditer(next_item_pattern, indexstr):
            pass
        if m:
            if verbose:
                print("Removing final next-item pattern", file=sys.stderr)
            indexstr = indexstr[:m.start()] \
                + "<br>\n<center><i>[end]</i></center>\n<br>\n" \
                + indexstr[m.end():]

        # Rewrite images to local, so we don't hit the network
        # trying to download images sites put directly in their RSS feeds.
        # If the images are the same as one already downloaded for a story,
        # we'll include it; otherwise we'll omit it.
        # (Test case: Popular Science, which includes large images
        # in its RSS even if the stories had a small inline image.)
        indexstr = parser.rewrite_images(indexstr)

        try:
            indexfile = os.path.join(outdir, "index.html")
            if verbose:
                print("Writing", indexfile, file=sys.stderr)
            with open(indexfile, "w") as index:
                # No longer support "ascii" option. Write the encoded unicode.
                index.write(indexstr)

                # Before the downloaded string, insert a final named anchor.
                # On some sites we get a bug where we accidentally write a >>
                # when there are really no further stories. So give it a
                # place to go. Though if the removal of the final >->
                # succeeded, this should no longer be needed.
                index.write("<p><a name=\"%d\">&nbsp;</a>\n" % (itemnum+1))

                # Write the downloaded feedme signature
                index.write(downloaded_string)

                index.write("\n</body>\n</html>\n")
                index.close()
        except Exception as e:
            msglog.err("Error writing index file! " + str(e))
            # msglog.err(str(sys.exc_info()[0]).encode('utf-8'))
            # msglog.err(str(sys.exc_info()[1]).encode('utf-8'))
            print(traceback.format_exc(tb), file=sys.stderr)

        # Update the cache for this site:
        if not nocache:
            cache[sitefeedurl] = newfeedcache
            # if verbose:
            #     print("Will update %s cache with:" % sitefeedurl,
            #           file=sys.stderr)
            #     print(str(newfeedcache), file=sys.stderr)

        ####################################################
        # Generate the output files
        #
        if 'plucker' in formats:
            make_plucker_file(indexfile, feedname, levels, ascii)
        if 'fb2' in formats:
            make_fb2_file(indexfile, feedname, levels, ascii)
        if 'epub' in formats:
            make_epub_file(indexfile, feedname, levels, ascii)

        #
        # All done. Update the cache file.
        # Note that we're rewriting the whole cache file on every site,
        # in case the process gets killed somewhere along the way.
        #
        if not nocache:
            if verbose:
                print(feedname, ": Updating cache file", file=sys.stderr)
            cache.save_to_file()
        elif verbose:
            print(feedname, ": Not updating cache file", file=sys.stderr)

    else:
        msglog.warn(feedname + ": no new content")

        # We may have made the directory. If so, remove it:
        # if there's no index file then there's no way to access anything there.
        if os.path.exists(outdir):
            print("Removing directory", outdir, file=sys.stderr)
            shutil.rmtree(outdir)

    # Done looping over items in this feed.
    # Try to rewrite the last page written to remove the next item links.
    # next_item_pattern = '<a href=\"#[0-9]+\">&gt;-&gt;</a></i></center>\n<br>\n'
    next_page_pattern = \
        '^<center><a href="[0-9]+.html">&gt;-[0-9]+-&gt;</a></center>$'
    if last_page_written:
        lastfile = os.path.join(outdir, last_page_written)
        if os.path.exists(lastfile):
            with open(lastfile) as lastfp:
                lastcontents = lastfp.read()
            try:
                with open(lastfile, 'w') as lastfp:
                    for line in lastcontents.split('\n'):
                        if re.match(next_page_pattern, line):
                            print("""
<center><i>((&nbsp;End %s&nbsp;))</i></center>"""
                              % feedname, file=lastfp)
                        else:
                            print(line, file=lastfp)
            except Exception as e:
                print("Couldn't open lastfile", lastfile, "for writing", e,
                        file=sys.stderr)
        elif verbose:
            print("lastfile", lastfile, "doesn't exist", file=sys.stderr)
    elif verbose:
        print("No pages written", file=sys.stderr)


#
# Main -- read the config file and loop over sites.
#
if __name__ == '__main__':
    import argparse

    usage = """
If no site is specified, feedme will update all the feeds in
~/.config/feedme.conf."""
    LongVersion = feedmeparser.VersionString + ": an RSS feed reader.\n\
Copyright 2017 by Akkana Peck; share and enjoy under the GPL v2 or later."

    parser = argparse.ArgumentParser(prog="feedme", description=usage)
    parser.add_argument('--version', action='version', version=LongVersion)
    parser.add_argument('feeds', type=str, nargs='*',
                        help="feeds to fetch")
    parser.add_argument("-n", "--nocache",
                         action="store_true", dest="nocache",
                         default=False,
                         help="Don't consult the cache, or update it")
    parser.add_argument("-s", "--show-sites",
                         action="store_true", dest="show_sites",
                         default=False,
                         help="Show available sites")
    parser.add_argument("-l", "--log", metavar="logfile",
                         action="store", dest="log_file_name",
                         help="Save output to a log file")
    parser.add_argument("-c", "--config-help",
                         action="store_true", dest="config_help",
                         default=False,
                         help="Print help on configuration files")
    options = parser.parse_args()
    # print("Parsed args. args:", options)

    config = feedmeparser.read_config_file()

    msglog = MsgLog()

    sections = config.sections()

    if options.config_help:
        print(LongVersion)
        print(ConfigHelp)
        sys.exit(0)

    if options.show_sites:
        for feedname in sections:
            print("%-25s %s" % (feedname, config.get(feedname, 'url')))
        sys.exit(0)

    if options.nocache:
        cache = None
        last_time = None
    else:
        try:
            cache = FeedmeCache.newcache()
        except Exception as e:
            # I don't know what causes a pickle error,
            # but cPickle.BadPickleGet can happen
            # and can be very hard to debug when it does.
            msglog.err("Error reading cache! " + str(e))
            # msglog.err(str(sys.exc_info()[0]).encode('utf-8'))
            # msglog.err(str(sys.exc_info()[1]).encode('utf-8'))
            ex_type, ex, tb = sys.exc_info()
            print(traceback.format_exc(tb), file=sys.stderr)

            # Refuse to run: unexpectedly running a whole feedme
            # without a cache is too painful.
            print("""
Can't read the cache file! Sorry, exiting.
Use -N to re-load all previously cached stories and reinitialize the cache.
""", file=sys.stderr)
            sys.exit(1)

        last_time = cache.last_time

    feeddir = expanduser(config.get('DEFAULT', 'dir'))
    if not os.path.exists(feeddir):
        os.makedirs(feeddir)
    logfilename = os.path.join(feeddir, 'LOG')

    # Set up a tee to the log file, and redirect stderr there:
    print("teeing output to", logfilename)
    stderrsav = sys.stderr
    outputlog = open(logfilename, "w", buffering=1, encoding='utf-8')
    sys.stderr = tee(stderrsav, outputlog)

    # Remove any obsolete feeds, no longer in the cnnfig file, from the cache.
    if cache and len(cache):
        feeds_to_remove = []
        for feedurl in cache:
            found = False
            for feedname in sections:
                if feedurl == config.get(feedname, 'url'):
                    found = True
                    break
            if not found:
                feeds_to_remove.append(feedurl)

        for feedurl in feeds_to_remove:
            print(feedurl, "is obsolete, will delete from cache", file=sys.stderr)
            del cache[feedurl]

    # Actually get the feeds.
    try:
        if options.feeds:
            for feed in options.feeds:
                print('Getting feed for', feed, file=sys.stderr)
                get_feed(feed, config, cache, last_time, msglog)
        else:
            for feedname in sections:
                # This can hang if feedparser hangs parsing the initial RSS.
                # So give the user a chance to ^C out of one feed
                # without stopping the whole run:
                try:
                    get_feed(feedname, config, cache, last_time, msglog)
                except KeyboardInterrupt:
                    print("Interrupt! Skipping feed", feedname, file=sys.stderr)
                    handle_keyboard_interrupt("Type q to quit, anything else to skip to next feed: ")
                    # No actual need to check the return value:
                    # handle_keyboard_interrupt will quit if the user types q.


    # This causes a lot of premature exits. Not sure why we end up
    # here rather than in the inner KeyboardInterrupt section.
    except KeyboardInterrupt:
        print("Caught keyboard interrupt at the wrong time!", file=sys.stderr)
        print(traceback.format_exc())
        #sys.exit(1)
    except OSError as e:
        print("Caught an OSError", file=sys.stderr)
        print(e, file=sys.stderr)
        #sys.exit(e.errno)

    try:
        # Now we're done. It's time to move the log file into its final place.
        datestr = time.strftime("%m-%d-%a")
        datedir = os.path.join(feeddir, datestr)
        print("Renaming", logfilename, "to", os.path.join(datedir, 'LOG'), file=sys.stderr)
        os.rename(logfilename,
                  os.path.join(datedir, 'LOG'))

        # and make a manifest listing all the files we downloaded.
        # This will be used remotely, so we don't want the local
        # path in it; everything is relative to this directory.
        # HTML files first, then image (and other) files,
        # so it's easy to skip images.
        # Each set of HTML files is preceded by its directory,
        # to make it easy for the fetcher to know when to mkdir.
        # MANIFEST should end with .EOF. on a line by itself
        # to avoid race conditions where the fetcher thinks it's
        # read the manifest while the file is still being written.
        discardchars = len(datedir)
        manifest = open(os.path.join(datedir, 'MANIFEST'), 'w')
        htmlfiles = []
        otherfiles = []
        for root, dirs, files in os.walk(datedir):
            shortroot = root[discardchars+1:]
            if shortroot:
                htmlfiles.append(shortroot + '/')
            for f in files:
                f = posixpath.join(shortroot, f)
                if f.endswith(".html"):
                    htmlfiles.append(f)
                else:
                    otherfiles.append(f)

        for f in htmlfiles:
            print(f, file=manifest)
        for f in otherfiles:
            print(f, file=manifest)

        print(".EOF.", file=manifest)
        manifest.close()
    except OSError as e:
        print("Couldn't move LOG or create MANIFEST:", file=sys.stderr)
        print(e)

    # Clean up old directories:
    clean_up(config)

    # Dump any errors we encountered.
    msgs = msglog.get_msgs()
    if msgs:
        print("\n===== Messages ====", file=sys.stderr)
        print(msgs, file=sys.stderr)
    msgs = msglog.get_errs()
    if msgs:
        print("\n====== Errors =====", file=sys.stderr)
        print(msgs, file=sys.stderr)
