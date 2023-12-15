#!/usr/bin/env python3

"""FeedmeCache and related code.
"""

import sys, os
import shutil
import time

# Use XDG for the config and cache directories if it's available
try:
    import xdg.BaseDirectory
except:
    pass

import msglog
import utils


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
            utils.ptraceback()

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

