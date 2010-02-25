from twisted.spread import pb
import os
class TempfileWriter(pb.Referenceable):
    """
    TempfileWriter class that just puts received data into a temporary file.
    """
    def __init__(self):
        self.buffer = os.tmpfile()

    def remote_write(self, data):
        self.buffer.write( data )

    def remote_close(self):
        pass


from twisted.python import log
from buildbot.process.buildstep import BuildStep
from boto.s3.connection import S3Connection
from boto.s3.key import Key
from buildbot.steps.transfer import StatusRemoteCommand, _TransferBuildStep
from buildbot.process.buildstep import SUCCESS, FAILURE, SKIPPED
class S3FileUpload(_TransferBuildStep):
    """
    Build step to transfer a file from the slave to the master.

    arguments:

    - ['slavesrc']   filename of source file at slave, relative to workdir
    - ['workdir']    string with slave working directory relative to builder
                     base dir, default 'build'
    - ['maxsize']    maximum size of the file, default None (=unlimited)
    - ['blocksize']  maximum size of each block being transfered
    - ['mode']       file access mode for the resulting master-side file.
                     The default (=None) is to leave it up to the umask of
                     the buildmaster process.

    """

    name = 'S3 upload'

    def __init__(self, slavesrc,
                 identifier=None, secret_identifier=None, 
                 bucket=None,
                 workdir=None, maxsize=None, blocksize=16*1024, mode='public-read',
                 **buildstep_kwargs):
        BuildStep.__init__(self, **buildstep_kwargs)
        self.addFactoryArguments(slavesrc=slavesrc,
                                 workdir=workdir,
                                 maxsize=maxsize,
                                 blocksize=blocksize,
                                 mode=mode,
                                 identifier=identifier,
                                 secret_identifier=secret_identifier,
                                 bucket=bucket
                                 )

        self.slavesrc = slavesrc
        self.workdir = workdir
        self.maxsize = maxsize
        self.blocksize = blocksize
        # modes from boto 
        assert mode in ['private', 'public-read', 'public-read-write', 'authenticated-write']
        self.mode = mode

        # Prepare S3 connection
        assert bucket is not None
        conn = S3Connection( identifier, secret_identifier )
        self.bk = conn.get_bucket( bucket )

    def start(self):
        version = self.slaveVersion("uploadFile")
        properties = self.build.getProperties()

        if not version:
            m = "slave is too old, does not know about uploadFile"
            raise BuildSlaveTooOldError(m)

        self.source = properties.render(self.slavesrc)

        self.step_status.setText(['uploading', os.path.basename(self.source)])
        log.msg("Uploading file from slave to temporary on master" )

        # we use maxsize to limit the amount of data on both sides
        self.fileWriter = TempfileWriter()

        # default arguments
        args = {
            'slavesrc': self.source,
            'workdir': self._getWorkdir(),
            'writer': self.fileWriter,
            'maxsize': self.maxsize,
            'blocksize': self.blocksize,
            }

        self.cmd = StatusRemoteCommand('uploadFile', args)
        d = self.runCommand(self.cmd)
        d.addCallback(self.gotFile).addErrback(self.failed)

    def gotFile( self, result ):
        # TODO: check if upload to master was successful
        
        self.step_status.setText(['Got file on master, sending to S3',])
        log.msg("Starting S3 upload" )

        self.fileWriter.buffer.seek(0)

        # get the key item
        k = Key(self.bk)
        # set key name
        k.key = self.source
        k.set_contents_from_file( self.fileWriter.buffer )
        k.set_acl( self.mode )

        BuildStep.finished( self, SUCCESS )

