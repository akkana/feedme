#!/usr/bin/env python3

##################################################################
# OUTPUT GENERATING FUNCTIONS FOR FEEDME
# Define functions for each output format you need to support.
#

def run_conversion_cmd(appargs):
    if verbose:
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
    outdir = os.path.join(utils.g_config.get('DEFAULT', 'dir'), extension[1:])
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
    if verbose:
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

