mythcommflag-silence
====================

MythTV commercial detection with silences

Code copy/pasted from http://www.mythtv.org/wiki/Commercial_detection_with_silences for convenience.

Requirements

   * Compilation environment (gcc, make) - sudo apt-get install build-essential
   * libsndfile for reading audio samples - sudo apt-get install libsndfile-dev
   * Python 2.7 for the new argument parser

Building

Build the silence executable using "make"
Install executables & Python script to /usr/local/bin/ using "sudo make install".

Use

/usr/local/bin/silence.py %JOBID% %VERBOSEMODE% --loglevel debug --presetfile /home/mythtv/.mythtv/silence.preset
