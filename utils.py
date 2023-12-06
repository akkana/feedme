#!/usr/bin/env python3

"""Some useful utilities for feedme, mostly config-file related.
"""

from configparser import ConfigParser

import sys, os
import traceback


VersionString = "FeedMe 1.1b5"


# The configuration object, once read in (in feedme.py main()),
# is global and read-only.
g_config = None


class MultiLineConfigParser(ConfigParser):
    """A ConfigParser that also makes it easy to get multi-line values
       as a list.
    """
    def get_multiline(self, feedname, configname):
        """Get a multiline config into a list, not a string"""
        configlines = self.get(feedname, configname)
        if configlines == '':
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
print("default_confdir:", g_default_confdir)


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
                print("Can't read", filepath)

    return g_config


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



