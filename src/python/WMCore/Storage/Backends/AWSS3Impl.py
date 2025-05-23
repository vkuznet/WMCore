#!/usr/bin/env python
"""
_AWSS3Impl_

Interface for AWS S3 CLI Stage Out Plugin

"""
from __future__ import print_function
from __future__ import division

import os
import logging
from WMCore.Storage.StageOutImpl import StageOutImpl
from WMCore.Storage.Registry import registerStageOutImpl

class AWSS3Impl(StageOutImpl):
    """
    _AWSS3Impl_

    Define the interface for AWS S3 CLI stage out plugin
    """

    def createSourceName(self, protocol, pfn):
        """
        _createSourceName_

        uses pfn

        """
        return "%s" % pfn

    def createStageOutCommand(self, sourcePFN, targetPFN, options=None, checksums=None, authMethod=None, forceMethod=False):
        """
        _createStageOutCommand_
        Build an aws s3 copy command
        """
        logging.warning("Warning! AWSS3Impl does not support authMethod handling")

        result = "#!/bin/sh\n"

        copyCommand = "aws s3 cp"

        if options != None:
            copyCommand += " %s " % options
        copyCommand += " %s " % sourcePFN
        copyCommand += " %s \n" % targetPFN

        result += copyCommand

        result += """
            EXIT_STATUS=$?
            echo "aws s3 cp exit status: $EXIT_STATUS"
            if [[ $EXIT_STATUS != 0 ]]; then
               echo "ERROR: Non-zero aws s3 cp Exit status!!!"
               echo "Cleaning up failed file:"
               %s
            fi
            exit $EXIT_STATUS
            """ % self.createRemoveFileCommand(targetPFN)

        return result
    
    def createDebuggingCommand(self, sourcePFN, targetPFN, options=None, checksums=None, authMethod=None, forceMethod=False):
        """
        Debug a failed aws s3 copy command for stageOut, without re-running it,
        providing information on the environment and the certifications

        :sourcePFN: str, PFN of the source file
        :targetPFN: str, destination PFN
        :options: str, additional options for copy command
        :checksums: dict, collect checksums according to the algorithms saved as keys
        """
        # Build the command for debugging purposes
        copyCommand = "aws s3 cp"
        if options != None:
            copyCommand += " %s " % options
        copyCommand += " %s " % sourcePFN
        copyCommand += " %s \n" % targetPFN

        result = self.debuggingTemplate.format(copy_command=copyCommand, source=sourcePFN, destination=targetPFN)
        return result

    def removeFile(self, pfnToRemove):
        """
        _removeFile_
        CleanUp pfn provided
        """
        command = ""
        if pfnToRemove.startswith("s3://"):
            command = "aws s3 rm %s" % pfnToRemove
        elif pfnToRemove.startswith("/"):
            command = "/bin/rm -f %s" % pfnToRemove
        elif os.path.isfile(pfnToRemove):
            command = "/bin/rm -f %s" % os.path.abspath(pfnToRemove)
        self.executeCommand(command)

    def createRemoveFileCommand(self, pfn):
        """
        return the command to delete a file after a failed copy
        """
        if pfn.startswith("s3://"):
            return "aws s3 rm %s" % pfn
        elif pfn.startswith("/"):
            return "/bin/rm -f %s" % pfn
        elif os.path.isfile(pfn):
            return "/bin/rm -f %s" % os.path.abspath(pfn)
        else:
            return ""

registerStageOutImpl("awss3", AWSS3Impl)
