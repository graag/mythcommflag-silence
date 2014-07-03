#!/usr/bin/env python
# Build a skiplist from silence in the audio track.
# Roger Siddons v1.0
# v2.0 Fix progid for job/player messages
# v3.0 Send player messages via Python
# v3.1 Fix commflag status, pad preset. Improve style & make Python 3 compatible
# v4.0 silence.cpp will kill the head of the pipeline (tail) when recording finished
# v4.1 Use unicode for foreign chars
# v4.2 Prevent BE writeStringList errors

import MythTV
import os
import subprocess
import argparse
import collections
import re
import sys

kExe_Silence = '/usr/local/bin/silence'
kUpmix_Channels = '6' # Change this to 2 if you never have surround sound in your recordings.


class MYLOG(MythTV.MythLog):
    "A specialised logger"

    def __init__(self, db):
        "Initialise logging"
        MythTV.MythLog.__init__(self, 'm', db)

    def log(self, msg, level=MythTV.MythLog.INFO):
        "Log message"
        # prepend string to msg so that rsyslog routes it to correct logfile
        MythTV.MythLog.log(self, MythTV.MythLog.COMMFLAG, level,
            'mythcommflag: ' + msg.rstrip('\n'))


class PRESET:
    "Manages the presets (parameters passed to the detection algorithm)"

    # define arg ordering and default values
    argname = ['thresh', 'minquiet', 'mindetect', 'minbreak', 'maxsep', 'pad']
    argval  = [  -75,       0.16,        6,          120,       120,    0.48]
    # dictionary holds value for each arg
    argdict = collections.OrderedDict(list(zip(argname, argval)))

    def _validate(self, k, v):
        "Converts arg input from string to float or None if invalid/not supplied"
        if v is None or v == '':
            return k, None
        try:
            return k, float(v)
        except ValueError:
            self.logger.log('Preset ' + k + ' (' + str(v) + ') is invalid - will use default',
                MYLOG.ERR)
            return k, None

    def __init__(self, _logger):
        "Initialise preset manager"
        self.logger = _logger

    def getFromArg(self, line):
        "Parses preset values from command-line string"
        self.logger.log('Parsing presets from "' + line + '"', MYLOG.DEBUG)
        if line:  # ignore empty string
            vals = [i.strip() for i in line.split(',')]  # split individual params
            # convert supplied values to float & match to appropriate arg name
            validargs = list(map(self._validate, self.argname, vals[0:len(self.argname)]))
            # remove missing/invalid values from list & replace default values with the rest
            self.argdict.update(v for v in validargs if v[1] is not None)

    def getFromFile(self, filename, title, callsign):
        "Gets preset values from a file"
        self.logger.log('Using preset file "' + filename + '"', MYLOG.DEBUG)
        try:
            with open(filename) as presets:
                for rawline in presets:
                    line = rawline.strip()
                    if line and (not line.startswith('#')):  # ignore empty & comment lines
                        vals = [i.strip() for i in line.split(',')]  # split individual params
                        # match preset name to recording title or channel
                        pattern = re.compile(vals[0], re.IGNORECASE)
                        if pattern.match(title) or pattern.match(callsign):
                            self.logger.log('Using preset "' + line.strip() + '"')
                            # convert supplied values to float & match to appropriate arg name
                            validargs = list(map(self._validate, self.argname,
                                                 vals[1:1 + len(self.argname)]))
                            # remove missing/invalid values from list &
                            # replace default values with the rest
                            self.argdict.update(v for v in validargs if v[1] is not None)
                            break
                else:
                    self.logger.log('No preset found for "' + title.encode('utf-8') + '" or "' + callsign.encode('utf-8') + '"')
        except IOError:
            self.logger.log('Presets file "' + filename + '" not found', MYLOG.ERR)
        return self.argdict

    def getValues(self):
        "Returns params as a list of strings"
        return [str(i) for i in list(self.argdict.values())]


def main():
    "Commflag a recording"

    # define options
    parser = argparse.ArgumentParser(description='Commflagger')
    parser.add_argument('--preset',
        help='Specify values as "Threshold, MinQuiet, MinDetect, MinLength, MaxSep, Pad"')
    parser.add_argument('--presetfile', help='Specify file containing preset values')
    parser.add_argument('--chanid', help='Use chanid for manual operation')
    parser.add_argument('--starttime', help='Use starttime for manual operation')
    parser.add_argument('jobid', nargs='?', help='Myth job id')

    # must set up log attributes before Db locks them
    MYLOG.loadArgParse(parser)
    MYLOG._setmask(MYLOG.COMMFLAG)

    # parse options
    args = parser.parse_args()

    db = MythTV.MythDB()
    logger = MYLOG(db)
    be = MythTV.BECache(db=db)

    if args.jobid:
        job = MythTV.Job(args.jobid, db)
        chanid = job.chanid
        starttime = job.starttime
    elif args.chanid and args.starttime:
        job = None
        chanid = args.chanid
        starttime = args.starttime
    else:
        logger.log('Both chanid and starttime must be specified', MYLOG.ERR)
        sys.exit(1)

    # get recording
    try:
        rec = MythTV.Recorded((chanid, starttime), db)
    except:
        if job:
            job.update({'status': job.ERRORED, 'comment': 'ERROR: Could not find recording.'})
        logger.log('Could not find recording', MYLOG.ERR)
        sys.exit(1)

    channel = MythTV.Channel(chanid, db)

    logger.log('')
    logger.log('Processing: ' + channel.callsign.encode('utf-8') + ', ' + str(rec.starttime)
        + ', "' + rec.title.encode('utf-8') + ' - ' + rec.subtitle.encode('utf-8')+ '"')

    sg = MythTV.findfile(rec.basename, rec.storagegroup, db)
    if sg is None:
        if job:
            job.update({'status': job.ERRORED,
                'comment': 'ERROR: Local access to recording not found.'})
        logger.log('Local access to recording not found', MYLOG.ERR)
        sys.exit(1)

    # player update message needs prog id (with time in Qt::ISODate format)
    progId = str(chanid) + '_' + str(starttime).replace(' ', 'T')

    # create params with default values
    param = PRESET(logger)
    # read any supplied presets
    if args.preset:
        param.getFromArg(args.preset)
    elif args.presetfile:  # use preset file
        param.getFromFile(args.presetfile, rec.title, channel.callsign)

    infile = os.path.join(sg.dirname, rec.basename)

    # Purge any existing skip list and flag as in-progress
    rec.commflagged = 2
    rec.markup.clean()
    rec.update()

    # Write out the file contents and keep going till recording is finished.
    p1 = subprocess.Popen(["tail", "--follow", "--bytes=+1", infile], stdout=subprocess.PIPE)
    # Pipe through ffmpeg to extract uncompressed audio stream.
    p2 = subprocess.Popen(["mythffmpeg", "-loglevel", "quiet", "-i", "pipe:0",
                          "-f", "au", "-ac", kUpmix_Channels, "-"],
                          stdin=p1.stdout, stdout=subprocess.PIPE)
    # Pipe to silence which will spit out formatted log lines
    p3 = subprocess.Popen([kExe_Silence, "%d" % p1.pid] + param.getValues(), stdin=p2.stdout,
                          stdout=subprocess.PIPE)

    # Process log output
    breaks = 0
    level = {'info': MYLOG.INFO, 'debug': MYLOG.DEBUG, 'err': MYLOG.ERR}
    while True:
        line = p3.stdout.readline()
        if line:
            flag, info = line.split('@', 1)
            if flag == 'cut':
                # extract numbers from log
                numbers = re.findall('\d+', info)
                logger.log(info)
                # mark advert in database
                rec.markup.append(int(numbers[0]), rec.markup.MARK_COMM_START, None)
                rec.markup.append(int(numbers[1]), rec.markup.MARK_COMM_END, None)
                rec.update()
                breaks += 1
                # send advert skiplist to MythPlayers
                tuplelist = [(str(x) + ':' + str(rec.markup.MARK_COMM_START),
                             str(y) + ':' + str(rec.markup.MARK_COMM_END))
                             for x, y in rec.markup.getskiplist()]
                mesg = 'COMMFLAG_UPDATE ' + progId + ' ' \
                    + ','.join([x for tuple in tuplelist for x in tuple])
                # logger.log('  Sending ' + mesg,  MYLOG.DEBUG)
                result = be.backendCommand("MESSAGE[]:[]" + mesg)
                if result != 'OK':
                    logger.log('Backend message failed, response = %s, message = MESSAGE[]:[]%s'
                               % (result, mesg), MYLOG.ERR)
            elif flag in level:
                logger.log(info, level.get(flag))
            else:  # unexpected prefix
                # use warning for unexpected log levels
                logger.log(flag, MYLOG.WARNING)
        else:
            break

    # Signal comflagging has finished
    rec.commflagged = 1
    rec.update()

    if job:
        job.update({'status': 272, 'comment': 'Detected %s adverts.' % breaks})
    logger.log('Detected %s adverts.' % breaks)

    # Finishing too quickly can cause writeStringList/socket errors in the BE. 
    # A short delay prevents this
    import time
    time.sleep(1)

if __name__ == '__main__':
    main()
