#!/usr/bin/env python3

# feedme: download RSS/Atom feeds and convert to HTML, epub, Plucker,
# or other formats suitable for offline reading on a handheld device,
#
# Copyright 2009-2023 by Akkana Peck <akkana@shallowsky.com>
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
  order
    The order of the feeds you'd like to see: an ordered list
    (one name per line) of the full names (not filenames) of each feed.
    Feed directories will have _01, _02 etc. prepended.
    Feeds not listed in order will be sorted alphabetically after
    the ordered ones.

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
    Level 1.5: only read the RSS page, but make story pages from it
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
import output_fmt

import urllib.error
import socket
import posixpath
import unicodedata
from datetime import datetime

# sheesh, this is apparently the recommended way to parse RFC 2822 dates:
import email.utils as email_utils

# FeedMe's module for parsing HTML inside feeds:
import pageparser

from tee import tee
import msglog

from cache import FeedmeCache
from utils import falls_between, last_time_this_feed, expanduser

# Rewriting image URLs to local ones
import imagecache

# utilities, mostly config-file related:
import utils

# For parsing sites that don't have RSS, just HTML with a list of links
import htmlindex

# Allow links in top page content.
# Feedparser 6.0 has dropped _HTMLSanitizer.acceptable_elements,
# but the documentation says that now it allows a and img by default.
# https://pythonhosted.org/feedparser/html-sanitization.html
try:
    feedparser._HTMLSanitizer.acceptable_elements.add('a')
    feedparser._HTMLSanitizer.acceptable_elements.add('img')
except AttributeError:
    print("Don't know how to whitelist elements in feedparser",
          feedparser.__version__, file=sys.stderr)

from bs4 import BeautifulSoup

# For importing helper modules
import importlib
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                             "helpers"))

verbose = False


#
# Clean up old feed directories
#
def clean_up():
    try:
        days = int(utils.g_config.get('DEFAULT', 'save_days'))
        feedsdir = expanduser(utils.g_config.get('DEFAULT', 'dir'))
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
            # Files never to delete:
            if f in ['feedme.dat', 'feeds.css', 'darkfeeds.css',
                     'LOG', 'urlrss.log' ]:
                continue

            f = os.path.join(dirname, f)

            # Logical xor: if rmdir is set, or it's not a directory,
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

    print("Cleaning up files older than %d days from feed and cache dirs"
          % days)
    clean_up_dir(feedsdir, True)
    clean_up_dir(cachedir, False)

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

def parse_name_from_conf_file(feedfile):
    """Given the full pathname to a .conf file name,
       return the site name from the initial [The Site Name] line.
    """
    with open(feedfile) as fp:
        for line in fp:
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                return line[1:-1].strip()
                # Could also do line.strip('][')
    return None


allow_unicode = False

def slugify(value, allow_unicode=False):
    """
    Taken from https://github.com/django/django/blob/master/django/utils/text.py
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single underscores. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Convert to lowercase. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value) \
                           .encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value)
    return re.sub(r'[-\s]+', '_', value).strip('-_')


# A number prepended to each feed if the user specifies feed order.
g_feednum = 0

#
# Get a single feed
#
def get_feed(feedname, cache, last_time, msglog):
    """Fetch a single site's feed.
       feedname can be the feed's config name ("Washington Post")
       or the conf file name ("washingtonpost" or "washingtonpost.conf").
    """
    global verbose, g_feednum

    verbose = (utils.g_config.get("DEFAULT", 'verbose').lower() == 'true')

    # Mandatory arguments:
    try:
        sitefeedurl = utils.g_config.get(feedname, 'url')
        feedsdir = utils.g_config.get(feedname, 'dir')
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
                               utils.g_default_confdir ))
        else:
            feedfile = os.path.join(utils.g_default_confdir, feedname)
            if os.path.exists(feedfile):
                fakefeedname = parse_name_from_conf_file(feedfile)
            if not sitefeedurl and not feedfile.endswith(".conf"):
                feedfile += ".conf"
                if os.path.exists(feedfile):
                    fakefeedname = parse_name_from_conf_file(feedfile)

        if fakefeedname:
            try:
                sitefeedurl = utils.g_config.get(fakefeedname, 'url')
                feedsdir = utils.g_config.get(fakefeedname, 'dir')
                feedname = fakefeedname
            except:
                if verbose:
                    print(feedname, "isn't a site feed name either",
                          file=sys.stderr)

    if not sitefeedurl:
        msglog.err("Can't find a config for: " + feedname)
        return

    verbose = (utils.g_config.get(feedname, 'verbose').lower() == 'true')
    levels = float(utils.g_config.get(feedname, 'levels'))

    feedsdir = expanduser(feedsdir)
    todaystr = time.strftime("%m-%d-%a")
    feedsdir = os.path.join(feedsdir, todaystr)

    formats = utils.g_config.get(feedname, 'formats').split(',')
    encoding = utils.g_config.get(feedname, 'encoding')
    ascii = utils.g_config.getboolean(feedname, 'ascii')
    skip_links = utils.g_config.getboolean(feedname, 'skip_links')
    skip_link_pats = utils.g_config.get_multiline(feedname, 'skip_link_pats')
    skip_title_pats = utils.g_config.get_multiline(feedname, 'skip_title_pats')

    user_agent = utils.g_config.get(feedname, 'user_agent')

    if verbose:
        print("\n=============\nGetting %s feed" % feedname, file=sys.stderr)
        print(datetime.now())

    # Is this a feed we should only check occasionally?
    """Does this feed specify only gathering at certain times?
       If so, has such a time passed since the last time the
       cache file was written?
    """
    when = utils.g_config.get(feedname, "when")
    if when and when != '' and last_time:
        if not falls_between(when, last_time, time.localtime()):
            print("Skipping", feedname, "-- not", when, file=sys.stderr)
            return
        print("Yes, it's time to feed:", when, file=sys.stderr)

    #encoding = utils.g_config.get(feedname, 'encoding')

    print("\n============\nfeedname:", feedname, file=sys.stderr)
    # Make it a legal and sane dirname
    feednamedir = slugify(feedname)

    # Make sure the link is at least some minimum width.
    # This is for viewers that have special areas defined on the
    # screen, e.g. areas for paging up/down or adjusting brightness.
    minwidth = utils.g_config.getint(feedname, 'min_width')

    # Is there already a feednamedir, with or without a prepended order number?
    # If it has an index.html in it, then feedme has already fed this
    # site today, and should bail rather than overwriting what's
    # already there.
    if os.path.exists(feedsdir):
        for d in os.listdir(feedsdir):
            if d.endswith(feednamedir):
                if os.path.exists(os.path.join(feedsdir, d, "index.html")):
                    print("Already fed %s: not overwriting" % d)
                    return
                # Partially fed this site earlier today, but didn't finish.
                # Continue, but note the fact on stderr.
                if verbose:
                    print("Partially fed %s: will overwrite old files" % d)

    # If the user specified an order, prepend its number
    g_feednum += 1
    try:
        order = utils.g_config.get_multiline('DEFAULT', 'order')
        feednamedir = "%02d_%s" % (g_feednum, feednamedir)
    except:
        pass

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
        feed_helper = utils.g_config.get(feedname, 'feed_helper')
    except:
        feed_helper = None
        try:
            page_helper = utils.g_config.get(feedname, 'page_helper')
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
        confoptions = utils.g_config.options(feedname)
        helper_args = {}
        for opt in confoptions:
            if opt.startswith("helper_"):
                key = opt[7:]
                if key:
                    helper_args[key] = utils.g_config.get(feedname, opt)
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
    last_fed_this = last_time_this_feed(cache, feedname)
    if verbose:
        print("Last fetched %s on %s" % (feedname, str(last_fed_this)),
              file=sys.stderr)

    if cache == None:
        nocache = True
    else:
        nocache = (utils.g_config.get(feedname, 'nocache') == 'true')
    if verbose and nocache:
        msglog.msg(feedname + ": Ignoring cache")

    downloaded_string ="\n<hr><i>(Downloaded by " + \
        utils.VersionString + ")</i>\n"

    html_index_links = utils.g_config.get(feedname, 'html_index_links')

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
        print("Parsing feed %s" % (sitefeedurl), file=sys.stderr)

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
        if html_index_links:
            feed = htmlindex.parse(feedname, html_index_links, verbose=verbose)
        elif sitefeedurl.startswith("file://"):
            feed = feedparser.parse(sitefeedurl)
        else:
            downloader = pageparser.FeedmeURLDownloader(feedname,
                                                        verbose=verbose)
            rss_str = downloader.download_url(sitefeedurl)
            feed = feedparser.parse(rss_str)
            rss_str = None
            response = None

    # except xml.sax._exceptions.SAXException, e:
    except urllib.error.HTTPError as e:
        print("HTTP error parsing URL:", sitefeedurl, file=sys.stderr)
        print(str(e), file=sys.stderr)
        return

    except pageparser.CookieError as e:
        msglog.err("No cookies, skipping site")
        msglog.err("Error was: %s" % e.message)
        msglog.err("Cookiefile details: %s\n" % e.longmessage)
        return

    except ValueError as e:
        msglog.err("Exception fetching URL %s: %s" % (url, e))
        return

    except Exception as e:
        print("Couldn't parse feed: URL:", sitefeedurl, file=sys.stderr)
        print(str(e), file=sys.stderr)
        # raise(e)
        utils.ptraceback()
        # print(traceback.format_exc())
        return

    # feedparser has no error return! One way is to check len(feed.feed).
    # Which makes no sense sicne feed is an object, why should it have a length?
    # if len(feed.feed) == 0:
    if len(feed.entries) == 0:
        msglog.err("Can't read " + sitefeedurl)
        return

    # XXX Sometimes feeds die a few lines later getting feed.feed.title.
    # Here's a braindead guard against it -- but why isn't this
    # whole clause inside a try? It should be.
    try:
        title = feed.feed.title
    except:
        title = None
    # if not 'title' in feed.feed:
    if not title:
        msglog.msg(sitefeedurl + " lacks a title!")
        feed.feed.title = '[' + feedname + ']'

    if cache and not nocache:
        try:
            feedcachedict = cache.thedict[sitefeedurl]
        except:
            feedcachedict = []
    newfeedcachedict = []

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

    try:
        urlrewrite = utils.g_config.get_multiline(feedname, 'story_url_rewrite')
        if urlrewrite:
            print("**** urlrewrite:", urlrewrite, file=sys.stderr)
    except:
        urlrewrite = None

    days = int(utils.g_config.get('DEFAULT', 'save_days'))
    too_old = time.time() - days * 60 * 60 * 24
    print("too_old would be", too_old, "(now is", time.time(), ")",
          file=sys.stderr)

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
            # (e.g. Washington Post), which doesn't have href either.
            #
            if 'links' in item:
                hrefs = [str(i['href']) for i in item.links
                        if 'rel' in i and 'href' in i
                        and i['rel'] == 'alternate']
            elif 'link' in item:
                hrefs = [ str(item.link) ]
            else:
                hrefs = []

            if 'id' in item:
                item_id = str(item.id)
                if verbose:
                    print("\nID %s" % item_id, file=sys.stderr)
            elif 'guid' in item:
                item_id = str(item.guid)
                if verbose:
                    print("\nGUID %s" % item_id, file=sys.stderr)
            elif hrefs:
                item_id = str(hrefs[0])
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

            # See if we've already seen this page's ID in this run.
            try:
                pagenum = suburls.index(item_id)
                # We've already seen a link to this URL.
                # That could mean it's a link to a different named anchor
                # within the same file, or it could mean that it's just
                # a duplicate (see below).
                if verbose:
                    print("already seen item id", item_id, "this run: skipping",
                          file=sys.stderr)
                    continue
            except ValueError:
                if verbose:
                    print("haven't seen item id", item_id, "yet this run",
                          file=sys.stderr)

            # Is it a duplicate story that we've already seen in this run?
            # Some sites, like Washington Post, repeat the same stories
            # multiple times on their RSS feed, but stories won't be
            # added to our real feedcachedict until we've succeeded in
            # fetching the whole site. So check the temporary cache.
            # On the other hand, Risks Digest has a single story and
            # a lot of RSS entries with links to #name tags in that story.
            # So in that case we should include the entry but not
            # re-fetch the story.
            if newfeedcachedict and item_id in newfeedcachedict:
                if verbose:
                    print("%s repeated today -- skipping" % item_id,
                          file=sys.stderr)
                continue

            # How about the title? Have we already seen that before?
            # Washington Post runs the same story (same title) several
            # times with different URLs.
            # But on other sites, like the Los Alamos Daily Post Legal Notices,
            # titles can be as simple as "LEGAL NOTICE" and are often dups,
            # but the actual stories/links are different.
            if item.title in titles and not utils.g_config.getboolean(
                    feedname, 'allow_dup_titles'):
                print('Skipping repeated title with a new ID: "%s", ID "%s"' \
                      % (item.title, item_id), file=sys.stderr)
                continue
            titles.append(item.title)

            # Get the published date.
            # item.pubDate is a unicode string, supposed to be in format
            # Thu, 11 Aug 2016 14:46:50 GMT (or +0000)
            # email.utils.parsedate returns a tuple.
            # Pass it to time.mktime() to get seconds since epoch.
            # XXX feedparser now has published_parsed which is
            # a time.struct_time. Can we count on that and not
            # have to do parsing here?
            try:
                pub_date = time.mktime(email_utils.parsedate(item.published))
            except Exception as e:
                pub_date = None
                if verbose:
                    print("Couldn't read real pubdate:", e, file=sys.stderr)

            # Haven't seen it yet this run. But is it in the cache already?
            if not nocache:
                # We want it in the cache, whether it's new or not:
                if verbose:
                    print("Will cache as %s" % item_id, file=sys.stderr)
                if item_id not in newfeedcachedict:
                    newfeedcachedict.append(item_id)
                if item_id in feedcachedict:
                    if verbose:
                        print("Seen it before, it's in the cache",
                              file=sys.stderr)

                    # We've seen this ID before. HOWEVER, it may still
                    # be new: a site might have a static URL for the
                    # monthly photo contest that gets updated once
                    # a month with all-new content.
                    if not utils.g_config.getboolean(feedname, 'allow_repeats'):
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
                        print("Recent change, re-fetching", file=sys.stderr)

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
                    if pub_date <= too_old and not nocache:
                        msglog.warn("%s is so old (%s -> %s) it's expired from the cache -- skipping" \
                                    % (item_id, str(item.published),
                                       str(pub_date)))
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

            # Okay, we're including this item.

            itemnum += 1
            if verbose:
                print("Item:", item_title, file=sys.stderr)

            # Now itemnum is the number of the entry on the index page;
            # pagenum is the html file of the subentry, e.g. 3.html.

            # Make the directory for this feed if we haven't already
            if not os.access(outdir, os.W_OK):
                if verbose:
                    print("Making", outdir, file=sys.stderr)
                os.makedirs(outdir)

            if 'author' in item:
                author = str(item.author)
            else:
                author = None

            content = get_content(item)

            # A parser is mostly needed for levels > 1, but even with
            # levels=1 we'll use it at the end for rewriting images
            # in the index string and weeding out skip_nodes.

            parser = pageparser.FeedmeHTMLParser(feedname)

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
                        if levels == 1.5:
                            # On sites that put the full story in the RSS
                            # entry, we can use that for the story,
                            # no need to fetch another file.
                            htmlstr = content
                            print("Level 1.5: content length is", len(htmlstr),
                                  file=sys.stderr)
                        else:
                            htmlstr = None

                            if urlrewrite:
                                if len(urlrewrite) == 2:
                                    oldlink = item_link
                                    item_link = re.sub(urlrewrite[0],
                                                       urlrewrite[1],
                                                       item_link)
                                    print("Rewrote", oldlink, "to", item_link,
                                          file=sys.stderr)
                                else:
                                    print("story_url_rewrite had wrong # args:",
                                          len(urlrewrite), urlrewrite,
                                          file=sys.stderr)

                        parser.fetch_url(item_link,
                                         outdir, fnam,
                                         title=item_title, author=author,
                                         footer=footer, html=htmlstr,
                                         user_agent=user_agent)

                        # On level 1.5 sites, import any changes just made
                        # to the final output.
                        # On level 2 sites, pageparser didn't change the
                        # index page, just sub-pages so this wouldn't help.
                        # XXX should be a better way to get that from
                        # the pageparser, and a way to get pageparser
                        # to clean the index page for levels 1 and 2.
                        if levels == 1.5:
                            justwrotefile = os.path.join(outdir, fnam)
                            if os.path.exists(justwrotefile):
                                with open(justwrotefile) as justwrotefp:
                                    content = justwrotefp.read()
                            elif verbose:
                                print("File", justwrotefile,
                                      "doesn't exist, can't read back",
                                      file=sys.stderr)

                    last_page_written = fnam

                except pageparser.NoContentError as e:
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
                # XXX Should add a note to the index page about what happened.
                except socket.timeout as e:
                    errmsg = "Socket.timeout error on title " + item_title
                    errmsg += "\nBreaking -- hopefully we'll write index.html"
                    msglog.err(errmsg)

                    # Include a note in the indexstr
                    indexstr += '<p>Socket timeout for <a href="%s">%s</a>\n' \
                                % (item_link, item_title)

                    if utils.g_config.get(feedname,
                                          'continue_on_timeout') == 'true':
                        continue
                    break

                # Handle timeouts in Python 2.6
                except urllib.error.URLError as e:
                    if isinstance(e.reason, socket.timeout):
                        errmsg = "URLError Socket.timeout on title "
                        errmsg += item_title
                        errmsg += "\n"
                        indexstr += "<p>" + errmsg
                        if utils.g_config.get(feedname,
                                      'continue_on_timeout') == 'true':
                            errmsg += "continue_on_timeout is true"
                            msglog.err(errmsg)
                            continue
                        errmsg += "Breaking -- hopefully we'll write index.html"
                        msglog.err(errmsg)

                        indexstr += \
                            '<p>Socket timeout for <a href="%s">%s</a>\n' \
                            % (item_link, item_title)
                        break

                    # Some other type of URLError.
                    errmsg = 'URLError on <a href="%s">%s</a><br>\n%s<br>\n' \
                             % (item_link, item_link, str(e))
                    msglog.err(errmsg)
                    indexstr += "<p><b>" + errmsg + "</b>"

                    indexstr += '<p>URLerror for <a href="%s">%s</a>: %s\n' \
                                % (item_link, item_title, str(e))
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

                    # Put a note in the index string
                    indexstr += "<pre>" + errmsg + "</pre>"

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

            if not content:
                content = "[No content]"

            # Sites that put too much formatting crap in the RSS:
            if utils.g_config.getboolean(feedname, 'simplify_rss'):
                content = pageparser.simplify_html(content) + " ... "

            # There's an increasing trend to load up RSS pages with images.
            # Try to remove them if skip_images is true,
            # as well as any links that contain only an image.
            if utils.g_config.getboolean(feedname, 'skip_images'):
                # XXX Rewrite to use parser rather than re
                content = re.sub('<a [^>]*href=.*> *<img .*?></a>', '', content)
                content = re.sub('<img .*?>', '', content)

            # If fetching images, download/rewrite images in the RSS too.
            # But for levels==1.5, the RSS content has already passed
            # through pageparser once and has already had images rewritten.
            elif levels != 1.5 and content.strip():
                content = imagecache.rewrite_images(content, sitefeedurl,
                                                    outdir, feedname)

            # Try to get rid of embedded links if skip_links is true:
            if skip_links:
                content = re.sub('<a href=.*>(.*?)</a>', '\\1', content)
            # If we're keeping links, don't keep empty ones:
            else:
                content = re.sub('<a  [^>]*href=.*> *</a>', '', content)

            # Delete any nodes specified for skipping
            content = pageparser.delete_skipped_nodes(content, feedname)

            # Skip any text specified in index_skip_content_pats.
            # Some sites (*cough* Pro Publica *cough*) do weird things
            # like putting huge <style> sections in the RSS.
            index_skip_pats = utils.g_config.get_multiline(feedname,
                                                   'index_skip_content_pats')
            for pat in index_skip_pats:
                content = re.sub(pat, '', content)
                author = re.sub(pat, '', author)

            # Some sites put the entire story in the description,
            # sometimes with a lot of formatting crap that tends to
            # make the text unreadable (color or font size).
            # If entrysize is specified, strip all the crap and
            # limit total length.
            entrysize = int(utils.g_config.get(feedname, 'rss_entry_size'))
            if entrysize:
                content = content[:entrysize]

                # This sometimes messes up the rest of the feed,
                # because it can include unclosed tags like <ul>.
                # Fix this by parsing it as its own mini HTML page,
                # then serializing, which closes all tags.
                soup = BeautifulSoup(content, "lxml")

                # While we're here: in the levels==1.5 case,
                # the H1 headline is getting into the feed, even though it
                # mostly duplicates the entry title. So remove the first H1,
                # if any.
                h1 = soup.find('h1')
                if h1:
                    h1.decompose()

                # Try to append an ellipsis at the end of the last
                # text element.
                try:
                    last_text = soup.find_all(string=True)[-1]
                    ltstring = str(last_text)
                    last_text.string.replace_with(ltstring + " ...")

                    content = ''.join([str(c) for c in soup.body.children])
                except Exception as e:
                    # If it didn't work, just add the ellipsis after
                    # the last element. It will probably show up on a
                    # line by itself.
                    print("Problem adding ellipsis:", e, file=sys.stderr)
                    content = ''.join([str(c) for c in soup.body.children]) \
                        +  " [...]"

                # In level 1.5, the '>-2->' and other footer matter
                # may have gotten appended. Don't want this in the blurb
                # on the index page:
                m = re.search(r'<center><a href="[0-9]+.html">&gt;-[0-9]+-&gt;</a></center>', content)
                if m:
                    content = content[:m.span()[0]]

            # Clear space for images: nmpoliticalreport has a big
            # image for each feed that's taller than the feed text.
            content += '\n<br clear="all">\n'

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
                indexstr += "\n<br><center>[[" + itemlink + short_title + "</a>]]</center>\n\n"

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
                if itemlink:
                    print(itemlink, file=sys.stderr)
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
        # indexstr = imagecache.rewrite_images(indexstr)

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

        #
        # Done with stories from this site. Update the cache file.
        # Note the whole cache file gets rewritten on every site,
        # in case the process gets killed somewhere along the way.
        #
        if not nocache:
            cache.add_items(sitefeedurl, newfeedcachedict)
            # if verbose:
            #     print("Updating %s cache with:" % sitefeedurl,
            #           file=sys.stderr)
            #     print(cache[sitefeedurl], file=sys.stderr)
            cache.save_to_file()
        elif verbose:
            print(feedname, ": Not updating cache file", file=sys.stderr)

        #
        # Generate any non-HTML output files
        #
        if 'plucker' in formats:
            output_fmt.make_plucker_file(indexfile, feedname, levels, ascii)
        if 'fb2' in formats:
            output_fmt.make_fb2_file(indexfile, feedname, levels, ascii)
        if 'epub' in formats:
            output_fmt.make_epub_file(indexfile, feedname, levels, ascii)


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

    if verbose:
        print("Done fetching feed", feedname, datetime.now(), file=sys.stderr)


def get_content(item):
    # Add either the content or the summary.
    # Prefer content since it might have links.
    if 'content' in item:
        return str(item.content[0].value) + "\n"
    if 'summary_detail' in item:
        return str(item.summary_detail.value) + "\n"
    if 'summary' in item:
        return str(item.summary.value) + "\n"
    if 'description' in item:
        return str(item.description.value) + "\n"
    return ""


def user_sort(feednames):
    """Given a list of feed names, return a list of feed names
       sorted by the user's 'order' config, otherwise alphabetically
       by title.
       The order file need not include all feed names;
       any that are missing will be listed alphabetically
       after the ones that are listed.
    """
    try:
        order = utils.g_config.get_multiline('DEFAULT', 'order')
    except:
        order = None
    if not order:
        return sorted(feednames)

    orderedlist = []
    for name in order:
        if name in feednames:
            orderedlist.append(name)
            feednames.remove(name)

    feednames.sort()

    return orderedlist + feednames


def main():
    import argparse

    usage = """
If no site is specified, feedme will update all the feeds in
~/.config/feedme.conf."""
    LongVersion = utils.VersionString + """: an RSS feed reader.
Copyright 2017-2024 by Akkana Peck;
share and enjoy under the GPL v2 or later."""

    parser = argparse.ArgumentParser(prog="feedme", description=usage)
    parser.add_argument('-v', '--version', action='version',
                        version=LongVersion)
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

    if options.config_help:
        print(LongVersion)
        print(ConfigHelp)
        sys.exit(0)

    utils.read_config_file()

    sections = utils.g_config.sections()

    if options.show_sites:
        for feedname in sections:
            print("%-25s %s" % (feedname, utils.g_config.get(feedname, 'url')))
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

    feeddir = expanduser(utils.g_config.get('DEFAULT', 'dir'))
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
                if feedurl == utils.g_config.get(feedname, 'url'):
                    found = True
                    break
            if not found:
                feeds_to_remove.append(feedurl)

        for feedurl in feeds_to_remove:
            print(feedurl, "is obsolete, will delete from cache",
                  file=sys.stderr)
            del cache[feedurl]

    #
    # Actually get the feeds.
    #
    try:
        if options.feeds:
            for feed in options.feeds:
                print('Getting feed for', feed, file=sys.stderr)
                get_feed(feed, cache, last_time, msglog)
        else:
            # Sort feeds according to user preference.
            # Sections is a list of user-friendly feed names.
            for feedname in user_sort(sections):
                # This can hang if feedparser hangs parsing the initial RSS.
                # So give the user a chance to ^C out of one feed
                # without stopping the whole run:
                try:
                    get_feed(feedname, cache, last_time, msglog)
                except KeyboardInterrupt:
                    print("Interrupt! Skipping feed", feedname,
                          file=sys.stderr)
                    handle_keyboard_interrupt(
                        "Type q to quit, anything else to skip to next feed: ")
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
        utils.ptraceback()
        # print(e, file=sys.stderr)
        # sys.exit(e.errno)

    try:
        # Now we're done. It's time to move the log file into its final place.
        datestr = time.strftime("%m-%d-%a")
        datedir = os.path.join(feeddir, datestr)
        print("Renaming", logfilename, "to", os.path.join(datedir, 'LOG'),
              file=sys.stderr)
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
    clean_up()

    # Dump any errors we encountered.
    msgs = msglog.get_msgs()
    if msgs:
        print("\n===== Messages ====", file=sys.stderr)
        print(msgs, file=sys.stderr)
    msgs = msglog.get_errs()
    if msgs:
        print("\n====== Errors =====", file=sys.stderr)
        print(msgs, file=sys.stderr)


#
# Main -- read the config file and loop over sites.
#
if __name__ == '__main__':
    main()
