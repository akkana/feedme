#!/usr/bin/env python3

"""Some useful utilities for feedme, mostly config-file and time related.
"""

from configparser import ConfigParser

import time
import sys, os
import traceback


VersionString = "FeedMe 1.1b6"


# The configuration object, once read in (in feedme.py main()),
# is global and read-only.
g_config = None


class MultiLineConfigParser(ConfigParser):
    """A ConfigParser that also makes it easy to get multi-line values
       as a list.
    """
    def get_multiline(self, feedname, configname):
        """Get a multiline config into a list, not a string"""
        try:
            configlines = self.get(feedname, configname)
            if configlines == '':
                return []
        except Exception as e:
            return []
        return configlines.split('\n')


#
# Keep track of the config file directory
#
g_default_confdir = None
def init_default_confdir():
    global g_default_confdir
    if 'XDG_CONFIG_HOME' in os.environ:
        confighome = os.environ['XDG_CONFIG_HOME']
    elif 'xdg.BaseDirectory' in sys.modules:
        confighome = xdg.BaseDirectory.xdg_config_home
    else:
        confighome = os.path.join(os.environ['HOME'], '.config')

    g_default_confdir = os.path.join(confighome, 'feedme')

init_default_confdir()
print("default_confdir:", g_default_confdir, file=sys.stderr)


#
# Read the configuration files
#
def read_config_file(confdir=None):
    '''Read the config file from XDG_CONFIG_HOME/feedme/*.conf,
       returning a ConfigParser object'''

    global g_config

    if not confdir:
        confdir = g_default_confdir

    main_conf_file = 'feedme.conf'
    conffile = os.path.join(confdir, main_conf_file)
    if not os.access(conffile, os.R_OK):
        print("Error: no config file in", conffile, file=sys.stderr)
        sys.exit(1)

    g_config = MultiLineConfigParser( {
        'url' : '',
        'verbose' : 'false',
        'levels' : '2',
        'formats' : 'none',
        'encoding' : '',  # blank means try several
        'page_start' : '',
        'page_end' : '',
        'single_page_pats' : '',
        'url_substitute' : '',
        'simplify_rss' : 'false',
        'rss_entry_size' : '0',  # max size in bytes

        # Patterns to skip within a story.
        # Anything within the regexps will be excised from the story.
        'skip_pats' : '',

        # Nodes to skip within a story, e.g. div class="advertisement"
        'skip_nodes' : '',

        # Various triggers for skipping a whole story:
        # Skip links with these patterns:
        'skip_link_pats' : '',
        # Skip anything with titles containing these:
        'skip_title_pats' : '',
        # Skip anything whose content includes these:
        'skip_content_pats' : '',
        # Skip anything where the index content includes:
        'index_skip_content_pats' : '',

        # acceptable alternate sources for images:
        'alt_domains' : '',

        # Rewrite URLs to stories? Two lines if defined, from_pat, to_pat
        'story_url_rewrite' : '',

        # Index page is HTML, not RSS/Atom? How are links specified?
        # This is a string indicating how to locate story links,
        # e.g. 'div class="layout-homepage__lite"'
        # Multiple attributes are allowed.
        'html_index_links' : '',

        # module for special URL downloading:
        'page_helper' : '',
        # Single string argument passed to the helper.
        'helper_arg' : '',

        'nocache' : 'false',
        'allow_repeats': 'false',
        'logfile' : '',
        'save_days' : '7',
        'skip_images' : 'true',
        'nonlocal_images' : 'false',
        'block_nonlocal_images' : 'true',
        'max_image_size' : '1200',
        'skip_links' : 'false',
        'when' : '',  # Day, like tue, or month-day, like 14
        'min_width' : '25', # min # chars in an item link
        'continue_on_timeout' : 'false',
        'user_agent' : VersionString,
        'ascii' : 'false',
        'allow_gzip' : 'true'
    } )

    g_config.read(conffile)
    for fil in os.listdir(confdir):
        if fil.endswith('.conf') and fil != main_conf_file:
            filepath = os.path.join(confdir, fil)
            if os.access(filepath, os.R_OK):
                try:
                    g_config.read(filepath)
                except Exception as e:
                    print("Couldn't parse site file %s: %s"
                          % (filepath, e))
            else:
                print("Can't read", filepath, file=sys.stderr)

    return g_config


def expanduser(name):
    """Do what os.path.expanduser does, but also allow $HOME in paths"""
    # utils.g_config.get alas doesn't substitute $HOME or ~
    if name[0:2] == "~/":
        name = os.path.join(os.environ['HOME'], name[2:])
    elif name[0:6] == "$HOME/":
        name = os.path.join(os.environ['HOME'], name[6:])
    return name


def last_time_this_feed(feeddir):
    '''Return the last time we fetched a given feed.
       This is most useful for feeds that randomly show old entries.
       Pass in the intended outdir, e.g. .../feeds/08-11-Thu/feedname
       Returns seconds since epoch.
    '''
    # XXX really should store this in the cache file
    # so it's not so dependent on how often we feed or clean up the cache.
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


# Python3 seems to have no straightforward way to just print a
# simple traceback without going into several levels of recursive
# "During handling of the above exception, another exception occurred"
# if there's anything involved that might have a nonascii character.
# This doesn't work reliably either:
# TypeError: unorderable types: int() < traceback() in the print line.
# or, more recently,
# '>=' not supported between instances of 'traceback' and 'int'
def ptraceback():
    try:
        # This tends to raise an exception,
        #    traceback unorderable types: traceback() >= int()
        # for no reason anyone seems to know:
        # ex_type, ex, tb = sys.exc_info()
        # print(str(traceback.format_exc(tb)), file=sys.stderr)
        # so instead:

        print("\n====== Stack trace was:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print("====== end stack trace\n", file=sys.stderr)
    except Exception as e:
        print("******** Yikes! Exception trying to print traceback:", e,
              file=sys.stderr)



