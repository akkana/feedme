#!/usr/bin/env python3

# A FeedMe helper module that copies files from a fixed location.


import time
import shutil
import os, sys

from utils import expanduser


def fetch_feed(target_dir, helper_args):
    """Copy a full feed, including index.html and all stories and images,
       to the target directory. Return a list of files copied
       (relative to the target directory, not full paths).
       If the source dir contains index.html, all files in it will be copied.
       Returns a list of the files copied.
    """

    if "srcdir" in helper_args:
        srcdir = expanduser(helper_args["srcdir"])

    if os.path.exists(os.path.join(srcdir, "index.html")):
        return copy_files(srcdir, target_dir)

    print("Couldn't find a feed for today under", srcdir, file=sys.stderr)
    return None


def copy_files(srcdir, dstdir):
    children = os.listdir(srcdir)
    copied = []

    try:
        os.mkdir(dstdir)
    except FileExistsError:
        pass
    if not os.path.isdir(dstdir):
        print("Couldn't make directory", dstdir, file=sys.stderr)
        return None

    for child in children:
        if os.path.isdir(child):
            continue
        shutil.copy(os.path.join(srcdir, child), dstdir)
        copied.append(child)

    print("Copied", len(copied), "files", file=sys.stderr)
    return copied


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: %s source_dir target_dir" % os.path.basename(sys.argv[0]))
        sys.exit(1)

    copied = fetch_feed(sys.argv[2], sys.argv[1])

    print("Copied %d files:" % len(copied))
    for f in copied:
        print(" ", f)

