# -*- test-case-name: buildbot.test.test_config -*-

import os, warnings, exceptions

from twisted.trial import unittest
from twisted.python import failure
from twisted.internet import defer

from buildbot.master import BuildMaster
from buildbot import scheduler, db
from twisted.application import service, internet
from twisted.spread import pb
from twisted.web.server import Site
from twisted.web.distrib import ResourcePublisher
from buildbot.process.builder import Builder
from buildbot.process.factory import BasicBuildFactory, ArgumentsInTheWrongPlace
from buildbot.changes.pb import PBChangeSource
from buildbot.changes.mail import SyncmailMaildirSource
from buildbot.schedulers.basic import Scheduler
from buildbot.steps.source import CVS, Darcs
from buildbot.steps.shell import Compile, Test, ShellCommand
from buildbot.status import base
from buildbot.steps import dummy, maxq, python, python_twisted, shell, \
     source, transfer
words = None
try:
    from buildbot.status import words
except ImportError:
    pass
from buildbot.test.runutils import ShouldFailMixin

emptyCfg = \
"""
from buildbot.buildslave import BuildSlave
BuildmasterConfig = c = {}
c['slaves'] = []
c['schedulers'] = []
c['builders'] = []
c['slavePortnum'] = 9999
c['projectName'] = 'dummy project'
c['projectURL'] = 'http://dummy.example.com'
c['buildbotURL'] = 'http://dummy.example.com/buildbot'
"""

buildersCfg = \
"""
from buildbot.process.factory import BasicBuildFactory
from buildbot.buildslave import BuildSlave
BuildmasterConfig = c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
c['slavePortnum'] = 9999
f1 = BasicBuildFactory('cvsroot', 'cvsmodule')
c['builders'] = [{'name':'builder1', 'slavename':'bot1',
                  'builddir':'workdir', 'factory':f1}]
"""

buildersCfg2 = buildersCfg + \
"""
f1 = BasicBuildFactory('cvsroot', 'cvsmodule2')
c['builders'] = [{'name':'builder1', 'slavename':'bot1',
                  'builddir':'workdir', 'factory':f1}]
"""

buildersCfg3 = buildersCfg2 + \
"""
c['builders'].append({'name': 'builder2', 'slavename': 'bot1',
                      'builddir': 'workdir2', 'factory': f1 })
"""

buildersCfg4 = buildersCfg2 + \
"""
c['builders'] = [{ 'name': 'builder1', 'slavename': 'bot1',
                   'builddir': 'newworkdir', 'factory': f1 },
                 { 'name': 'builder2', 'slavename': 'bot1',
                   'builddir': 'workdir2', 'factory': f1 }]
"""

buildersCfg5 = buildersCfg2 + \
"""
from buildbot.config import BuilderConfig
c['builders'] = [
    BuilderConfig(
        name = 'builder1',
        slavename = 'bot1',
        builddir = 'newworkdir',
        factory = f1),
    BuilderConfig(
        name = 'builder2',
        slavename = 'bot1',
        builddir = 'workdir2',
        factory = f1)
]
"""


wpCfg1 = buildersCfg + \
"""
from buildbot.steps import shell
from buildbot.config import BuilderConfig
f1 = BasicBuildFactory('cvsroot', 'cvsmodule')
f1.addStep(shell.ShellCommand, command=[shell.WithProperties('echo')])
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1',
        builddir='workdir1', factory=f1)
]
"""

wpCfg2 = buildersCfg + \
"""
from buildbot.steps import shell
from buildbot.config import BuilderConfig
f1 = BasicBuildFactory('cvsroot', 'cvsmodule')
f1.addStep(shell.ShellCommand,
           command=[shell.WithProperties('echo %s', 'revision')])
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1',
        builddir='workdir1', factory=f1)
]
"""



ircCfg1 = emptyCfg + \
"""
from buildbot.status import words
c['status'] = [words.IRC('irc.us.freenode.net', 'buildbot', ['twisted'])]
"""

ircCfg2 = emptyCfg + \
"""
from buildbot.status import words
c['status'] = [words.IRC('irc.us.freenode.net', 'buildbot', ['twisted']),
               words.IRC('irc.example.com', 'otherbot', ['chan1', 'chan2'])]
"""

ircCfg3 = emptyCfg + \
"""
from buildbot.status import words
c['status'] = [words.IRC('irc.us.freenode.net', 'buildbot', ['knotted'])]
"""

webCfg1 = emptyCfg + \
"""
from buildbot.status import html
c['status'] = [html.Waterfall(http_port=9980)]
"""

webCfg2 = emptyCfg + \
"""
from buildbot.status import html
c['status'] = [html.Waterfall(http_port=9981)]
"""

webCfg3 = emptyCfg + \
"""
from buildbot.status import html
c['status'] = [html.Waterfall(http_port='tcp:9981:interface=127.0.0.1')]
"""

webNameCfg1 = emptyCfg + \
"""
from buildbot.status import html
c['status'] = [html.Waterfall(distrib_port='~/.twistd-web-pb')]
"""

webNameCfg2 = emptyCfg + \
"""
from buildbot.status import html
c['status'] = [html.Waterfall(distrib_port='./bar.socket')]
"""

debugPasswordCfg = emptyCfg + \
"""
c['debugPassword'] = 'sekrit'
"""

interlockCfgBad = \
"""
from buildbot.process.factory import BasicBuildFactory
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
f1 = BasicBuildFactory('cvsroot', 'cvsmodule')
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1),
    BuilderConfig(name='builder2', slavename='bot1', factory=f1),
]
# interlocks have been removed
c['interlocks'] = [('lock1', ['builder1'], ['builder2', 'builder3']),
                   ]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

lockCfgBad1 = \
"""
from buildbot.steps.dummy import Dummy
from buildbot.process.factory import BuildFactory, s
from buildbot.locks import MasterLock
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
l1 = MasterLock('lock1')
l2 = MasterLock('lock1') # duplicate lock name
f1 = BuildFactory([s(Dummy, locks=[])])
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1,
                  locks=[l1, l2]),
    BuilderConfig(name='builder2', slavename='bot1', factory=f1),
]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

lockCfgBad2 = \
"""
from buildbot.steps.dummy import Dummy
from buildbot.process.factory import BuildFactory, s
from buildbot.locks import MasterLock, SlaveLock
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
l1 = MasterLock('lock1')
l2 = SlaveLock('lock1') # duplicate lock name
f1 = BuildFactory([s(Dummy, locks=[])])
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1,
                  locks=[l1, l2]),
    BuilderConfig(name='builder2', slavename='bot1', factory=f1),
]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

lockCfgBad3 = \
"""
from buildbot.steps.dummy import Dummy
from buildbot.process.factory import BuildFactory, s
from buildbot.locks import MasterLock
from buildbot.buildslave import BuildSlave
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
l1 = MasterLock('lock1')
l2 = MasterLock('lock1') # duplicate lock name
f1 = BuildFactory([s(Dummy, locks=[l2])])
f2 = BuildFactory([s(Dummy)])
c['builders'] = [
                 { 'name': 'builder1', 'slavename': 'bot1',
                 'builddir': 'workdir', 'factory': f2, 'locks': [l1] },
                 { 'name': 'builder2', 'slavename': 'bot1',
                 'builddir': 'workdir2', 'factory': f1 },
                   ]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

lockCfg1a = \
"""
from buildbot.process.factory import BasicBuildFactory
from buildbot.locks import MasterLock
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
f1 = BasicBuildFactory('cvsroot', 'cvsmodule')
l1 = MasterLock('lock1')
l2 = MasterLock('lock2')
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1,
                  locks=[l1, l2]),
    BuilderConfig(name='builder2', slavename='bot1', factory=f1),
]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

lockCfg1b = \
"""
from buildbot.process.factory import BasicBuildFactory
from buildbot.locks import MasterLock
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
f1 = BasicBuildFactory('cvsroot', 'cvsmodule')
l1 = MasterLock('lock1')
l2 = MasterLock('lock2')
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1, locks=[l1]),
    BuilderConfig(name='builder2', slavename='bot1', factory=f1),
]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

# test out step Locks
lockCfg2a = \
"""
from buildbot.steps.dummy import Dummy
from buildbot.process.factory import BuildFactory, s
from buildbot.locks import MasterLock
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
l1 = MasterLock('lock1')
l2 = MasterLock('lock2')
f1 = BuildFactory([s(Dummy, locks=[l1,l2])])
f2 = BuildFactory([s(Dummy)])

c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1),
    BuilderConfig(name='builder2', slavename='bot1', factory=f2),
]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

lockCfg2b = \
"""
from buildbot.steps.dummy import Dummy
from buildbot.process.factory import BuildFactory, s
from buildbot.locks import MasterLock
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
l1 = MasterLock('lock1')
l2 = MasterLock('lock2')
f1 = BuildFactory([s(Dummy, locks=[l1])])
f2 = BuildFactory([s(Dummy)])

c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1),
    BuilderConfig(name='builder2', slavename='bot1', factory=f2),
]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

lockCfg2c = \
"""
from buildbot.steps.dummy import Dummy
from buildbot.process.factory import BuildFactory, s
from buildbot.locks import MasterLock
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
l1 = MasterLock('lock1')
l2 = MasterLock('lock2')
f1 = BuildFactory([s(Dummy)])
f2 = BuildFactory([s(Dummy)])

c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1),
    BuilderConfig(name='builder2', slavename='bot1', factory=f2),
]
c['slavePortnum'] = 9999
BuildmasterConfig = c
"""

schedulersCfg = \
"""
from buildbot.schedulers.basic import Scheduler
from buildbot.process.factory import BasicBuildFactory
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
f1 = BasicBuildFactory('cvsroot', 'cvsmodule')
b1 = BuilderConfig(name='builder1', slavename='bot1', factory=f1)
c['builders'] = [b1]
c['schedulers'] = [Scheduler('full', None, 60, ['builder1'])]
c['slavePortnum'] = 9999
c['projectName'] = 'dummy project'
c['projectURL'] = 'http://dummy.example.com'
c['buildbotURL'] = 'http://dummy.example.com/buildbot'
BuildmasterConfig = c
"""

class ConfigTest(unittest.TestCase, ShouldFailMixin):
    def setUp(self):
        # this class generates several deprecation warnings, which the user
        # doesn't need to see.
        warnings.simplefilter('ignore', exceptions.DeprecationWarning)

    def setup_master(self):
        if not os.path.exists(self.basedir):
            os.makedirs(self.basedir)
        spec = db.DB("sqlite3", os.path.join(self.basedir, "state.sqlite"))
        db.create_db(spec)
        self.buildmaster = BuildMaster(self.basedir, db=spec)
        self.buildmaster.readConfig = True
        #self.buildmaster.startService()

    def failUnlessListsEquivalent(self, list1, list2):
        l1 = list1[:]
        l1.sort()
        l2 = list2[:]
        l2.sort()
        self.failUnlessEqual(l1, l2)

    def servers(self, s, types):
        # perform a recursive search of s.services, looking for instances of
        # twisted.application.internet.TCPServer, then extract their .args
        # values to find the TCP ports they want to listen on
        for child in s:
            if service.IServiceCollection.providedBy(child):
                for gc in self.servers(child, types):
                    yield gc
            if isinstance(child, types):
                yield child

    def TCPports(self, s):
        return list(self.servers(s, internet.TCPServer))
    def UNIXports(self, s):
        return list(self.servers(s, internet.UNIXServer))
    def TCPclients(self, s):
        return list(self.servers(s, internet.TCPClient))

    def checkPorts(self, svc, expected):
        """Verify that the TCPServer and UNIXServer children of the given
        service have the expected portnum/pathname and factory classes. As a
        side-effect, return a list of servers in the same order as the
        'expected' list. This can be used to verify properties of the
        factories contained therein."""

        expTCP = [e for e in expected if type(e[0]) == int]
        expUNIX = [e for e in expected if type(e[0]) == str]
        haveTCP = [(p.args[0], p.args[1].__class__)
                   for p in self.TCPports(svc)]
        haveUNIX = [(p.args[0], p.args[1].__class__)
                    for p in self.UNIXports(svc)]
        self.failUnlessListsEquivalent(expTCP, haveTCP)
        self.failUnlessListsEquivalent(expUNIX, haveUNIX)
        ret = []
        for e in expected:
            for have in self.TCPports(svc) + self.UNIXports(svc):
                if have.args[0] == e[0]:
                    ret.append(have)
                    continue
        assert(len(ret) == len(expected))
        return ret

    def testEmpty(self):
        self.basedir = "config/configtest/empty"
        self.setup_master()
        self.failUnlessRaises(KeyError, self.buildmaster.loadConfig, "")

    def testSimple(self):
        # covers slavePortnum, base checker passwords
        self.basedir = "config/configtest/simple"
        self.setup_master()
        master = self.buildmaster

        d = master.loadConfig(emptyCfg)
        def _check(ign):
            # note: this doesn't actually start listening, because the app
            # hasn't been started running
            self.failUnlessEqual(master.slavePortnum, "tcp:9999")
            self.checkPorts(master, [(9999, pb.PBServerFactory)])
            self.failUnlessEqual(list(master.change_svc), [])
            self.failUnlessEqual(master.botmaster.builders, {})
            self.failUnlessEqual(master.checker.users,
                                 {"change": "changepw"})
            self.failUnlessEqual(master.projectName, "dummy project")
            self.failUnlessEqual(master.projectURL, "http://dummy.example.com")
            self.failUnlessEqual(master.buildbotURL,
                                 "http://dummy.example.com/buildbot")
        d.addCallback(_check)
        return d

    def testSlavePortnum(self):
        self.basedir = "config/configtest/slave_portnum"
        self.setup_master()
        master = self.buildmaster

        d = master.loadConfig(emptyCfg)
        def _check1(ign):
            self.failUnlessEqual(master.slavePortnum, "tcp:9999")
            ports = self.checkPorts(master, [(9999, pb.PBServerFactory)])
            p = ports[0]
            d = master.loadConfig(emptyCfg)
            def _check2(ign):
                self.failUnlessEqual(master.slavePortnum, "tcp:9999")
                ports = self.checkPorts(master, [(9999, pb.PBServerFactory)])
                self.failUnlessIdentical(p, ports[0],
                                         "the slave port was changed even "
                                         "though the configuration was not")
            d.addCallback(_check2)
            d.addCallback(lambda ign:
                          master.loadConfig(emptyCfg +
                                            "c['slavePortnum'] = 9000\n"))
            def _check3(ign):
                self.failUnlessEqual(master.slavePortnum, "tcp:9000")
                ports = self.checkPorts(master, [(9000, pb.PBServerFactory)])
                self.failIf(p is ports[0],
                            "slave port was unchanged "
                            "but configuration was changed")
            d.addCallback(_check3)
        d.addCallback(_check1)
        return d

    def testSlaves(self):
        self.basedir = "config/configtest/slaves"
        self.setup_master()
        master = self.buildmaster
        d = master.loadConfig(emptyCfg)
        def _check1(ign):
            self.failUnlessEqual(master.botmaster.builders, {})
            self.failUnlessEqual(master.checker.users,
                                 {"change": "changepw"})
            # 'botsCfg' is testing backwards compatibility, for 0.7.5 config
            # files that have not yet been updated to 0.7.6 . This
            # compatibility (and this test) is scheduled for removal in 0.8.0
        d.addCallback(_check1)
        botsCfg = (emptyCfg +
                   "c['bots'] = [('bot1', 'pw1'), ('bot2', 'pw2')]\n")
        d.addCallback(lambda ign: master.loadConfig(botsCfg))
        def _check2(ign):
            self.failUnlessEqual(master.checker.users,
                                 {"change": "changepw",
                                  "bot1": "pw1",
                                  "bot2": "pw2"})
        d.addCallback(_check2)
        d.addCallback(lambda ign: master.loadConfig(botsCfg))
        def _check3(ign):
            self.failUnlessEqual(master.checker.users,
                                 {"change": "changepw",
                                  "bot1": "pw1",
                                  "bot2": "pw2"})
        d.addCallback(_check3)
        d.addCallback(lambda ign: master.loadConfig(emptyCfg))
        def _check4(ign):
            self.failUnlessEqual(master.checker.users,
                                 {"change": "changepw"})
        d.addCallback(_check4)
        slavesCfg = (emptyCfg +
                     "from buildbot.buildslave import BuildSlave\n"
                     "c['slaves'] = [BuildSlave('bot1','pw1'), "
                     "BuildSlave('bot2','pw2')]\n")
        d.addCallback(lambda ign: master.loadConfig(slavesCfg))
        def _check5(ign):
            self.failUnlessEqual(master.checker.users,
                                 {"change": "changepw",
                                  "bot1": "pw1",
                                  "bot2": "pw2"})
        d.addCallback(_check5)
        return d

    def testChangeSource(self):
        self.basedir = "config/configtest/changesource"
        self.setup_master()
        master = self.buildmaster
        d = master.loadConfig(emptyCfg)
        def _check0(ign):
            self.failUnlessEqual(list(master.change_svc), [])
        d.addCallback(_check0)

        sourcesCfg = emptyCfg + \
"""
from buildbot.changes.pb import PBChangeSource
c['change_source'] = PBChangeSource()
"""

        d.addCallback(lambda ign: master.loadConfig(sourcesCfg))
        def _check1(res):
            self.failUnlessEqual(len(list(self.buildmaster.change_svc)), 1)
            s1 = list(self.buildmaster.change_svc)[0]
            self.failUnless(isinstance(s1, PBChangeSource))
            self.failUnlessEqual(s1, list(self.buildmaster.change_svc)[0])
            self.failUnless(s1.parent)

            # verify that unchanged sources are not interrupted
            d1 = self.buildmaster.loadConfig(sourcesCfg)

            def _check2(res):
                self.failUnlessEqual(len(list(self.buildmaster.change_svc)), 1)
                s2 = list(self.buildmaster.change_svc)[0]
                self.failUnlessIdentical(s1, s2)
                self.failUnless(s1.parent)
            d1.addCallback(_check2)
            return d1
        d.addCallback(_check1)

        # make sure we can get rid of the sources too
        d.addCallback(lambda res: self.buildmaster.loadConfig(emptyCfg))

        def _check3(res):
            self.failUnlessEqual(list(self.buildmaster.change_svc), [])
        d.addCallback(_check3)

        return d

    def testChangeSources(self):
        # make sure we can accept a list
        self.basedir = "config/configtest/changesources"
        self.setup_master()
        master = self.buildmaster
        d = master.loadConfig(emptyCfg)
        def _check0(ign):
            self.failUnlessEqual(list(master.change_svc), [])
        d.addCallback(_check0)

        sourcesCfg = emptyCfg + \
"""
from buildbot.changes.pb import PBChangeSource
from buildbot.changes.mail import SyncmailMaildirSource
c['change_source'] = [PBChangeSource(),
                     SyncmailMaildirSource('.'),
                    ]
"""

        d.addCallback(lambda ign: master.loadConfig(sourcesCfg))
        def _check1(res):
            self.failUnlessEqual(len(list(self.buildmaster.change_svc)), 2)
            s1,s2 = list(self.buildmaster.change_svc)
            if isinstance(s2, PBChangeSource):
                s1,s2 = s2,s1
            self.failUnless(isinstance(s1, PBChangeSource))
            self.failUnless(s1.parent)
            self.failUnless(isinstance(s2, SyncmailMaildirSource))
            self.failUnless(s2.parent)
        d.addCallback(_check1)
        return d

    def testSources(self):
        # test backwards compatibility. c['sources'] is deprecated.
        self.basedir = "config/configtest/sources"
        self.setup_master()
        master = self.buildmaster
        d = master.loadConfig(emptyCfg)
        def _check0(ign):
            self.failUnlessEqual(list(master.change_svc), [])
        d.addCallback(_check0)

        sourcesCfg = emptyCfg + \
"""
from buildbot.changes.pb import PBChangeSource
c['sources'] = [PBChangeSource()]
"""

        d.addCallback(lambda ign: master.loadConfig(sourcesCfg))
        def _check1(res):
            self.failUnlessEqual(len(list(self.buildmaster.change_svc)), 1)
            s1 = list(self.buildmaster.change_svc)[0]
            self.failUnless(isinstance(s1, PBChangeSource))
            self.failUnless(s1.parent)
        d.addCallback(_check1)
        return d

    def shouldBeFailure(self, res, *expected):
        self.failUnless(isinstance(res, failure.Failure),
                        "we expected this to fail, not produce %s" % (res,))
        res.trap(*expected)
        return None # all is good

    def testSchedulerErrors(self):
        self.basedir = "config/configtest/schedulererrors"
        self.setup_master()
        master = self.buildmaster
        d = master.loadConfig(emptyCfg)
        def _check1(ign):
            self.failUnlessEqual(master.allSchedulers(), [])
        d.addCallback(_check1)

        def _test(ign, cfg, which, err, substr):
            self.shouldFail(err, which, substr,
                            self.buildmaster.loadConfig, cfg)

        # c['schedulers'] must be a list
        badcfg = schedulersCfg + \
"""
c['schedulers'] = Scheduler('full', None, 60, ['builder1'])
"""
        d.addCallback(_test, badcfg, "one Scheduler instance", AssertionError,
                      "c['schedulers'] must be a list of Scheduler instances")

        # c['schedulers'] must be a list of IScheduler objects
        badcfg = schedulersCfg + \
"""
c['schedulers'] = ['oops', 'problem']
"""
        d.addCallback(_test, badcfg, "list of strings", AssertionError,
                      "c['schedulers'] must be a list of Scheduler instances")

        # c['schedulers'] must point at real builders
        badcfg = schedulersCfg + \
"""
c['schedulers'] = [Scheduler('full', None, 60, ['builder-bogus'])]
"""
        d.addCallback(_test, badcfg, "Scheduler with bogus builder",
                      AssertionError,
                      "uses unknown builder")

        # builderNames= must be a list
        badcfg = schedulersCfg + \
"""
c['schedulers'] = [Scheduler('full', None, 60, 'builder1')]
"""
        d.addCallback(_test, badcfg, "Scheduler with non-list", AssertionError,
                      "must be a list of Builder description names")

        # builderNames= must be a list of strings, not dicts
        badcfg = schedulersCfg + \
"""
c['schedulers'] = [Scheduler('full', None, 60, [b1])]
"""
        d.addCallback(_test, badcfg, "Scheduler with list of non-names",
                      AssertionError,
                      "must be a list of Builder description names")

        # builderNames= must be a list of strings, not a dict
        badcfg = schedulersCfg + \
"""
c['schedulers'] = [Scheduler('full', None, 60, b1)]
"""
        d.addCallback(_test, badcfg, "Scheduler with single non-name",
                      AssertionError,
                      "must be a list of Builder description names")

        # each Scheduler must have a unique name
        badcfg = schedulersCfg + \
"""
c['schedulers'] = [Scheduler('dup', None, 60, []),
                   Scheduler('dup', None, 60, [])]
"""
        d.addCallback(_test, badcfg, "non-unique Scheduler names", ValueError,
                      "Schedulers must have unique names")

        return d

    def testSchedulers(self):
        self.basedir = "config/configtest/schedulers"
        self.setup_master()
        master = self.buildmaster
        d = master.loadConfig(emptyCfg)
        d.addCallback(lambda ign:
                      self.failUnlessEqual(master.allSchedulers(), []))
        d.addCallback(lambda ign: self.buildmaster.loadConfig(schedulersCfg))
        d.addCallback(self._testSchedulers_1)
        return d

    def _testSchedulers_1(self, res):
        sch = self.buildmaster.allSchedulers()
        self.failUnlessEqual(len(sch), 1)
        s = sch[0]
        self.failUnless(isinstance(s, Scheduler))
        self.failUnlessEqual(s.name, "full")
        self.failUnlessEqual(s.branch, None)
        self.failUnlessEqual(s.treeStableTimer, 60)
        self.failUnlessEqual(s.builderNames, ['builder1'])

        newcfg = schedulersCfg + \
"""
s1 = Scheduler('full', None, 60, ['builder1'])
c['schedulers'] = [s1, Dependent('downstream', s1, ['builder1'])]
"""
        d = self.buildmaster.loadConfig(newcfg)
        d.addCallback(self._testSchedulers_2, newcfg)
        return d
    def _testSchedulers_2(self, res, newcfg):
        sch = self.buildmaster.allSchedulers()
        self.failUnlessEqual(len(sch), 2)
        s = sch[0]
        self.failUnless(isinstance(s, scheduler.Scheduler))
        s = sch[1]
        self.failUnless(isinstance(s, scheduler.Dependent))
        self.failUnlessEqual(s.name, "downstream")
        self.failUnlessEqual(s.builderNames, ['builder1'])

        # reloading the same config file should leave the schedulers in place
        d = self.buildmaster.loadConfig(newcfg)
        d.addCallback(self._testSchedulers_3, sch)
        return d
    def _testSchedulers_3(self, res, sch1):
        sch2 = self.buildmaster.allSchedulers()
        self.failUnlessEqual(len(sch2), 2)
        sch1.sort()
        sch2.sort()
        self.failUnlessEqual(sch1, sch2)
        self.failUnlessIdentical(sch1[0], sch2[0])
        self.failUnlessIdentical(sch1[1], sch2[1])
        self.failUnlessIdentical(sch1[0].parent, self.buildmaster)
        self.failUnlessIdentical(sch1[1].parent, self.buildmaster)



    def testBuilders(self):
        self.basedir = "config/configtest/builders"
        self.setup_master()
        master = self.buildmaster
        bm = master.botmaster
        d = master.loadConfig(emptyCfg)
        def _check1(ign):
            self.failUnlessEqual(bm.builders, {})
        d.addCallback(_check1)

        d.addCallback(lambda ign: master.loadConfig(buildersCfg))
        def _check2(ign):
            self.failUnlessEqual(bm.builderNames, ["builder1"])
            self.failUnlessEqual(bm.builders.keys(), ["builder1"])
            self.b = b = bm.builders["builder1"]
            self.failUnless(isinstance(b, Builder))
            self.failUnlessEqual(b.name, "builder1")
            self.failUnlessEqual(b.slavenames, ["bot1"])
            self.failUnlessEqual(b.builddir, "workdir")
            f1 = b.buildFactory
            self.failUnless(isinstance(f1, BasicBuildFactory))
            steps = f1.steps
            self.failUnlessEqual(len(steps), 3)
            self.failUnlessEqual(steps[0], (CVS,
                                            {'cvsroot': 'cvsroot',
                                             'cvsmodule': 'cvsmodule',
                                             'mode': 'clobber'}))
            self.failUnlessEqual(steps[1], (Compile,
                                            {'command': 'make all'}))
            self.failUnlessEqual(steps[2], (Test,
                                            {'command': 'make check'}))
        d.addCallback(_check2)
        # make sure a reload of the same data doesn't interrupt the Builder
        d.addCallback(lambda ign: master.loadConfig(buildersCfg))
        def _check3(ign):
            self.failUnlessEqual(bm.builderNames, ["builder1"])
            self.failUnlessEqual(bm.builders.keys(), ["builder1"])
            b2 = bm.builders["builder1"]
            self.failUnlessIdentical(self.b, b2)
            # TODO: test that the BuilderStatus object doesn't change
            #statusbag2 = master.client_svc.statusbags["builder1"]
            #self.failUnlessIdentical(statusbag, statusbag2)
        d.addCallback(_check3)

        # but changing something should result in a new Builder
        d.addCallback(lambda ign: master.loadConfig(buildersCfg2))
        def _check4(ign):
            self.failUnlessEqual(bm.builderNames, ["builder1"])
            self.failUnlessEqual(bm.builders.keys(), ["builder1"])
            self.b3 = b3 = bm.builders["builder1"]
            self.failIf(self.b is b3)
            # the statusbag remains the same TODO
            #statusbag3 = master.client_svc.statusbags["builder1"]
            #self.failUnlessIdentical(statusbag, statusbag3)
        d.addCallback(_check4)

        # adding new builder
        d.addCallback(lambda ign: master.loadConfig(buildersCfg3))
        def _check5(ign):
            self.failUnlessEqual(bm.builderNames, ["builder1", "builder2"])
            self.failUnlessListsEquivalent(bm.builders.keys(),
                                           ["builder1", "builder2"])
            self.b4 = b4 = bm.builders["builder1"]
            self.failUnlessIdentical(self.b3, b4)
        d.addCallback(_check5)

        # changing first builder should leave it at the same place in the list
        d.addCallback(lambda ign: master.loadConfig(buildersCfg4))
        def _check6(ign):
            self.failUnlessEqual(bm.builderNames, ["builder1", "builder2"])
            self.failUnlessListsEquivalent(bm.builders.keys(),
                                           ["builder1", "builder2"])
            b5 = bm.builders["builder1"]
            self.failIf(self.b4 is b5)
        d.addCallback(_check6)

        d.addCallback(lambda ign: master.loadConfig(buildersCfg5))
        def _check6a(ign):
            self.failUnlessEqual(bm.builderNames, ["builder1", "builder2"])
            self.failUnlessListsEquivalent(bm.builders.keys(),
                                           ["builder1", "builder2"])
        d.addCallback(_check6a)

        # and removing it should make the Builder go away
        d.addCallback(lambda ign: master.loadConfig(emptyCfg))
        def _check7(ign):
            self.failUnlessEqual(bm.builderNames, [])
            self.failUnlessEqual(bm.builders, {})
            #self.failUnlessEqual(master.client_svc.statusbags, {}) # TODO
        d.addCallback(_check7)
        return d

    def testWithProperties(self):
        self.basedir = "config/configtest/withproperties"
        self.setup_master()
        master = self.buildmaster
        d = master.loadConfig(wpCfg1)
        def _check1(ign):
            self.failUnlessEqual(master.botmaster.builderNames, ["builder1"])
            self.failUnlessEqual(master.botmaster.builders.keys(), ["builder1"])
            self.b1 = master.botmaster.builders["builder1"]
        d.addCallback(_check1)

        # reloading the same config should leave the builder unchanged
        d.addCallback(lambda ign: master.loadConfig(wpCfg1))
        def _check2(ign):
            b2 = master.botmaster.builders["builder1"]
            self.failUnlessIdentical(self.b1, b2)
        d.addCallback(_check2)

        # but changing the parameters of the WithProperties should change it
        d.addCallback(lambda ign: master.loadConfig(wpCfg2))
        def _check3(ign):
            self.b3 = b3 = master.botmaster.builders["builder1"]
            self.failIf(self.b1 is b3)
        d.addCallback(_check3)

        # again, reloading same config should leave the builder unchanged
        d.addCallback(lambda ign: master.loadConfig(wpCfg2))
        def _check4(ign):
            b4 = master.botmaster.builders["builder1"]
            self.failUnlessIdentical(self.b3, b4)
        d.addCallback(_check4)
        return d

    def checkIRC(self, m, expected):
        ircs = {}
        for irc in self.servers(m, words.IRC):
            ircs[irc.host] = (irc.nick, irc.channels)
        self.failUnlessEqual(ircs, expected)

    def testIRC(self):
        if not words:
            raise unittest.SkipTest("Twisted Words package is not installed")
        self.basedir = "config/configtest/IRC"
        self.setup_master()
        master = self.buildmaster
        d = master.loadConfig(emptyCfg)
        e1 = {}
        d.addCallback(lambda res: self.checkIRC(master, e1))
        d.addCallback(lambda res: master.loadConfig(ircCfg1))
        e2 = {'irc.us.freenode.net': ('buildbot', ['twisted'])}
        d.addCallback(lambda res: self.checkIRC(master, e2))
        d.addCallback(lambda res: master.loadConfig(ircCfg2))
        e3 = {'irc.us.freenode.net': ('buildbot', ['twisted']),
              'irc.example.com': ('otherbot', ['chan1', 'chan2'])}
        d.addCallback(lambda res: self.checkIRC(master, e3))
        d.addCallback(lambda res: master.loadConfig(ircCfg3))
        e4 = {'irc.us.freenode.net': ('buildbot', ['knotted'])}
        d.addCallback(lambda res: self.checkIRC(master, e4))
        d.addCallback(lambda res: master.loadConfig(ircCfg1))
        e5 = {'irc.us.freenode.net': ('buildbot', ['twisted'])}
        d.addCallback(lambda res: self.checkIRC(master, e5))
        return d

    def testWebPortnum(self):
        self.basedir = "config/configtest/webportnum"
        self.setup_master()
        master = self.buildmaster

        d = master.loadConfig(webCfg1)
        def _check1(res):
            ports = self.checkPorts(self.buildmaster,
                                    [(9999, pb.PBServerFactory), (9980, Site)])
            p = ports[1]
            self.p = p
        # nothing should be changed
        d.addCallback(_check1)

        d.addCallback(lambda res: self.buildmaster.loadConfig(webCfg1))
        def _check2(res):
            ports = self.checkPorts(self.buildmaster,
                                    [(9999, pb.PBServerFactory), (9980, Site)])
            self.failUnlessIdentical(self.p, ports[1],
                                     "web port was changed even though "
                                     "configuration was not")
        # WebStatus is no longer a ComparableMixin, so it will be
        # rebuilt on each reconfig
        #d.addCallback(_check2)

        d.addCallback(lambda res: self.buildmaster.loadConfig(webCfg2))
        # changes port to 9981
        def _check3(p):
            ports = self.checkPorts(self.buildmaster,
                                    [(9999, pb.PBServerFactory), (9981, Site)])
            self.failIf(self.p is ports[1],
                        "configuration was changed but web port was unchanged")
        d.addCallback(_check3)

        d.addCallback(lambda res: self.buildmaster.loadConfig(webCfg3))
        # make 9981 on only localhost
        def _check4(p):
            ports = self.checkPorts(self.buildmaster,
                                    [(9999, pb.PBServerFactory), (9981, Site)])
            self.failUnlessEqual(ports[1].kwargs['interface'], "127.0.0.1")
        d.addCallback(_check4)

        d.addCallback(lambda res: self.buildmaster.loadConfig(emptyCfg))
        d.addCallback(lambda res:
                      self.checkPorts(self.buildmaster,
                                      [(9999, pb.PBServerFactory)]))
        return d

    def testWebPathname(self):
        self.basedir = "config/configtest/webpathname"
        self.setup_master()
        master = self.buildmaster

        d = master.loadConfig(webNameCfg1)
        def _check1(res):
            self.checkPorts(self.buildmaster,
                            [(9999, pb.PBServerFactory),
                             ('~/.twistd-web-pb', pb.PBServerFactory)])
            unixports = self.UNIXports(self.buildmaster)
            self.f = f = unixports[0].args[1]
            self.failUnless(isinstance(f.root, ResourcePublisher))
        d.addCallback(_check1)

        d.addCallback(lambda res: self.buildmaster.loadConfig(webNameCfg1))
        # nothing should be changed
        def _check2(res):
            self.checkPorts(self.buildmaster,
                            [(9999, pb.PBServerFactory),
                             ('~/.twistd-web-pb', pb.PBServerFactory)])
            newf = self.UNIXports(self.buildmaster)[0].args[1]
            self.failUnlessIdentical(self.f, newf,
                                     "web factory was changed even though "
                                     "configuration was not")
        # WebStatus is no longer a ComparableMixin, so it will be
        # rebuilt on each reconfig
        #d.addCallback(_check2)

        d.addCallback(lambda res: self.buildmaster.loadConfig(webNameCfg2))
        def _check3(res):
            self.checkPorts(self.buildmaster,
                            [(9999, pb.PBServerFactory),
                             ('./bar.socket', pb.PBServerFactory)])
            newf = self.UNIXports(self.buildmaster)[0].args[1],
            self.failIf(self.f is newf,
                        "web factory was unchanged but "
                        "configuration was changed")
        d.addCallback(_check3)

        d.addCallback(lambda res: self.buildmaster.loadConfig(emptyCfg))
        d.addCallback(lambda res:
                      self.checkPorts(self.buildmaster,
                                      [(9999, pb.PBServerFactory)]))
        return d

    def testDebugPassword(self):
        self.basedir = "config/configtest/debugpassword"
        self.setup_master()
        master = self.buildmaster

        d = master.loadConfig(debugPasswordCfg)
        d.addCallback(lambda ign:
                      self.failUnlessEqual(master.checker.users,
                                           {"change": "changepw",
                                            "debug": "sekrit"}))

        d.addCallback(lambda ign: master.loadConfig(debugPasswordCfg))
        d.addCallback(lambda ign:
                      self.failUnlessEqual(master.checker.users,
                                           {"change": "changepw",
                                            "debug": "sekrit"}))

        d.addCallback(lambda ign: master.loadConfig(emptyCfg))
        d.addCallback(lambda ign:
                      self.failUnlessEqual(master.checker.users,
                                           {"change": "changepw"}))
        return d

    def testLocks(self):
        self.basedir = "config/configtest/locks"
        self.setup_master()
        master = self.buildmaster
        botmaster = master.botmaster
        # this is mostly synchronous, so we skip all the addCallbacks

        # make sure that c['interlocks'] is rejected properly
        self.failUnlessRaises(KeyError, master.loadConfig, interlockCfgBad)
        # and that duplicate-named Locks are caught
        self.failUnlessRaises(ValueError, master.loadConfig, lockCfgBad1)
        self.failUnlessRaises(ValueError, master.loadConfig, lockCfgBad2)
        self.failUnlessRaises(ValueError, master.loadConfig, lockCfgBad3)

        # create a Builder that uses Locks
        master.loadConfig(lockCfg1a)
        b1 = master.botmaster.builders["builder1"]
        self.failUnlessEqual(len(b1.locks), 2)

        # reloading the same config should not change the Builder
        master.loadConfig(lockCfg1a)
        self.failUnlessIdentical(b1, master.botmaster.builders["builder1"])
        # but changing the set of locks used should change it
        master.loadConfig(lockCfg1b)
        self.failIfIdentical(b1, master.botmaster.builders["builder1"])
        b1 = master.botmaster.builders["builder1"]
        self.failUnlessEqual(len(b1.locks), 1)

        # similar test with step-scoped locks
        master.loadConfig(lockCfg2a)
        b1 = master.botmaster.builders["builder1"]
        # reloading the same config should not change the Builder
        master.loadConfig(lockCfg2a)
        self.failUnlessIdentical(b1, master.botmaster.builders["builder1"])
        # but changing the set of locks used should change it
        master.loadConfig(lockCfg2b)
        self.failIfIdentical(b1, master.botmaster.builders["builder1"])
        b1 = master.botmaster.builders["builder1"]
        # remove the locks entirely
        master.loadConfig(lockCfg2c)
        self.failIfIdentical(b1, master.botmaster.builders["builder1"])

    def testNoChangeHorizon(self):
        master = self.buildmaster
        master.loadChanges()
        sourcesCfg = emptyCfg + \
"""
from buildbot.changes.pb import PBChangeSource
c['change_source'] = PBChangeSource()
"""
        d = master.loadConfig(sourcesCfg)
        def _check1(res):
            self.failUnlessEqual(len(list(self.buildmaster.change_svc)), 1)
            self.failUnlessEqual(self.buildmaster.change_svc.changeHorizon, 0)
        d.addCallback(_check1)
        return d

    def testChangeHorizon(self):
        master = self.buildmaster
        master.loadChanges()
        sourcesCfg = emptyCfg + \
"""
from buildbot.changes.pb import PBChangeSource
c['change_source'] = PBChangeSource()
c['changeHorizon'] = 5
"""
        d = master.loadConfig(sourcesCfg)
        def _check1(res):
            self.failUnlessEqual(len(list(self.buildmaster.change_svc)), 1)
            self.failUnlessEqual(self.buildmaster.change_svc.changeHorizon, 5)
        d.addCallback(_check1)
        return d

class ConfigElements(unittest.TestCase):
    # verify that ComparableMixin is working
    def testSchedulers(self):
        s1 = scheduler.Scheduler(name='quick', branch=None,
                                 treeStableTimer=30,
                                 builderNames=['quick'])
        s2 = scheduler.Scheduler(name="all", branch=None,
                                 treeStableTimer=5*60,
                                 builderNames=["a", "b"])
        s3 = scheduler.Try_Userpass("try", ["a","b"], port=9989,
                                    userpass=[("foo","bar")])
        s1a = scheduler.Scheduler(name='quick', branch=None,
                                  treeStableTimer=30,
                                  builderNames=['quick'])
        s2a = scheduler.Scheduler(name="all", branch=None,
                                  treeStableTimer=5*60,
                                  builderNames=["a", "b"])
        s3a = scheduler.Try_Userpass("try", ["a","b"], port=9989,
                                     userpass=[("foo","bar")])
        self.failUnless(s1 == s1)
        self.failUnless(s1 == s1a)
        self.failUnless(s1a in [s1, s2, s3])
        self.failUnless(s2a in [s1, s2, s3])
        self.failUnless(s3a in [s1, s2, s3])



class ConfigFileTest(unittest.TestCase):

    def testFindConfigFile(self):
        os.mkdir("test_cf")
        open(os.path.join("test_cf", "master.cfg"), "w").write(emptyCfg)
        slaveportCfg = emptyCfg + "c['slavePortnum'] = 9000\n"
        open(os.path.join("test_cf", "alternate.cfg"), "w").write(slaveportCfg)

        m = BuildMaster("test_cf")
        m.loadTheConfigFile()
        self.failUnlessEqual(m.slavePortnum, "tcp:9999")

        m = BuildMaster("test_cf", "alternate.cfg")
        m.loadTheConfigFile()
        self.failUnlessEqual(m.slavePortnum, "tcp:9000")


class MyTarget(base.StatusReceiverMultiService):
    def __init__(self, name):
        self.name = name
        base.StatusReceiverMultiService.__init__(self)
    def startService(self):
        # make a note in a list stashed in the BuildMaster
        self.parent.targetevents.append(("start", self.name))
        return base.StatusReceiverMultiService.startService(self)
    def stopService(self):
        self.parent.targetevents.append(("stop", self.name))
        return base.StatusReceiverMultiService.stopService(self)

class MySlowTarget(MyTarget):
    def stopService(self):
        from twisted.internet import reactor
        d = base.StatusReceiverMultiService.stopService(self)
        def stall(res):
            d2 = defer.Deferred()
            reactor.callLater(0.1, d2.callback, res)
            return d2
        d.addCallback(stall)
        m = self.parent
        def finishedStalling(res):
            m.targetevents.append(("stop", self.name))
            return res
        d.addCallback(finishedStalling)
        return d

# we can't actually startService a buildmaster with a config that uses a
# fixed slavePortnum like 9999, so instead this makes it possible to pass '0'
# for the first time, and then substitute back in the allocated port number
# on subsequent passes.
startableEmptyCfg = emptyCfg + \
"""
c['slavePortnum'] = %d
"""
                    
targetCfg1 = startableEmptyCfg + \
"""
from buildbot.test.test_config import MyTarget
c['status'] = [MyTarget('a')]
"""

targetCfg2 = startableEmptyCfg + \
"""
from buildbot.test.test_config import MySlowTarget
c['status'] = [MySlowTarget('b')]
"""

class StartService(unittest.TestCase):
    def tearDown(self):
        return self.master.stopService()

    def testStartService(self):
        os.mkdir("test_ss")
        self.master = m = BuildMaster("test_ss")
        # inhibit the usual read-config-on-startup behavior
        m.readConfig = True
        m.startService()
        d = m.loadConfig(startableEmptyCfg % 0)
        d.addCallback(self._testStartService_0)
        return d

    def _testStartService_0(self, res):
        m = self.master
        m.targetevents = []
        # figure out what port got allocated
        self.portnum = m.slavePort._port.getHost().port
        d = m.loadConfig(targetCfg1 % self.portnum)
        d.addCallback(self._testStartService_1)
        return d

    def _testStartService_1(self, res):
        self.failUnlessEqual(len(self.master.statusTargets), 1)
        self.failUnless(isinstance(self.master.statusTargets[0], MyTarget))
        self.failUnlessEqual(self.master.targetevents,
                             [('start', 'a')])
        self.master.targetevents = []
        # reloading the same config should not start or stop the target
        d = self.master.loadConfig(targetCfg1 % self.portnum)
        d.addCallback(self._testStartService_2)
        return d

    def _testStartService_2(self, res):
        self.failUnlessEqual(self.master.targetevents, [])
        # but loading a new config file should stop the old one, then
        # start the new one
        d = self.master.loadConfig(targetCfg2 % self.portnum)
        d.addCallback(self._testStartService_3)
        return d

    def _testStartService_3(self, res):
        self.failUnlessEqual(self.master.targetevents,
                             [('stop', 'a'), ('start', 'b')])
        self.master.targetevents = []
        # and going back to the old one should do the same, in the same
        # order, even though the current MySlowTarget takes a moment to shut
        # down
        d = self.master.loadConfig(targetCfg1 % self.portnum)
        d.addCallback(self._testStartService_4)
        return d

    def _testStartService_4(self, res):
        self.failUnlessEqual(self.master.targetevents,
                             [('stop', 'b'), ('start', 'a')])

cfg1 = \
"""
from buildbot.process.factory import BuildFactory, s
from buildbot.steps.shell import ShellCommand
from buildbot.steps.source import Darcs
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
BuildmasterConfig = c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
c['slavePortnum'] = 9999
f1 = BuildFactory([ShellCommand(command='echo yes'),
                   s(ShellCommand, command='old-style'),
                   ])
f1.addStep(Darcs(repourl='http://buildbot.net/repos/trunk'))
f1.addStep(ShellCommand, command='echo old-style')
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1),
]
"""

cfg1_bad = \
"""
from buildbot.process.factory import BuildFactory, s
from buildbot.steps.shell import ShellCommand
from buildbot.steps.source import Darcs
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
BuildmasterConfig = c = {}
c['slaves'] = [BuildSlave('bot1', 'pw1')]
c['schedulers'] = []
c['slavePortnum'] = 9999
f1 = BuildFactory([ShellCommand(command='echo yes'),
                   s(ShellCommand, command='old-style'),
                   ])
# it should be this:
#f1.addStep(ShellCommand(command='echo args'))
# but an easy mistake is to do this:
f1.addStep(ShellCommand(), command='echo args')
# this test makes sure this error doesn't get ignored
c['builders'] = [
    BuilderConfig(name='builder1', slavename='bot1', factory=f1),
]
"""

class Factories(unittest.TestCase):
    def printExpecting(self, factory, args):
        factory_keys = factory[1].keys()
        factory_keys.sort()
        args_keys = args.keys()
        args_keys.sort()

        print
        print "factory had:"
        for k in factory_keys:
            print k
        print "but we were expecting:"
        for k in args_keys:
            print k

    def failUnlessExpectedShell(self, factory, defaults=True, **kwargs):
        shell_args = {}
        if defaults:
            shell_args.update({'descriptionDone': None,
                               'description': None,
                               'workdir': None,
                               'logfiles': {},
                               'lazylogfiles': False,
                               'usePTY': "slave-config",
                               })
        shell_args.update(kwargs)
        self.failUnlessIdentical(factory[0], ShellCommand)
        if factory[1] != shell_args:
            self.printExpecting(factory, shell_args)
        self.failUnlessEqual(factory[1], shell_args)

    def failUnlessExpectedDarcs(self, factory, **kwargs):
        darcs_args = {'workdir': None,
                      'alwaysUseLatest': False,
                      'mode': 'update',
                      'timeout': 1200,
                      'retry': None,
                      'baseURL': None,
                      'defaultBranch': None,
                      'logfiles': {},
                      'lazylogfiles' : False,
                      }
        darcs_args.update(kwargs)
        self.failUnlessIdentical(factory[0], Darcs)
        if factory[1] != darcs_args:
            self.printExpecting(factory, darcs_args)
        self.failUnlessEqual(factory[1], darcs_args)

    def testSteps(self):
        m = BuildMaster(".")
        m.loadConfig(cfg1)
        b = m.botmaster.builders["builder1"]
        steps = b.buildFactory.steps
        self.failUnlessEqual(len(steps), 4)

        self.failUnlessExpectedShell(steps[0], command="echo yes")
        self.failUnlessExpectedShell(steps[1], defaults=False,
                                     command="old-style")
        self.failUnlessExpectedDarcs(steps[2],
                                     repourl="http://buildbot.net/repos/trunk")
        self.failUnlessExpectedShell(steps[3], defaults=False,
                                     command="echo old-style")

    def testBadAddStepArguments(self):
        m = BuildMaster(".")
        self.failUnlessRaises(ArgumentsInTheWrongPlace, m.loadConfig, cfg1_bad)

    def _loop(self, orig):
        step_class, kwargs = orig.getStepFactory()
        newstep = step_class(**kwargs)
        return newstep

    def testAllSteps(self):
        # make sure that steps can be created from the factories that they
        # return
        for s in ( dummy.Dummy(), dummy.FailingDummy(), dummy.RemoteDummy(),
                   maxq.MaxQ("testdir"),
                   python.BuildEPYDoc(), python.PyFlakes(),
                   python_twisted.HLint(),
                   python_twisted.Trial(testpath=None, tests="tests"),
                   python_twisted.ProcessDocs(), python_twisted.BuildDebs(),
                   python_twisted.RemovePYCs(),
                   shell.ShellCommand(), shell.TreeSize(),
                   shell.Configure(), shell.Compile(), shell.Test(),
                   source.CVS("cvsroot", "module"),
                   source.SVN("svnurl"), source.Darcs("repourl"),
                   source.Git("repourl"),
                   source.Arch("url", "version"),
                   source.Bazaar("url", "version", "archive"),
                   source.Bzr("repourl"),
                   source.Mercurial("repourl"),
                   source.P4("p4base"),
                   source.P4Sync(1234, "p4user", "passwd", "client",
                                 mode="copy"),
                   source.Monotone("server", "branch"),
                   transfer.FileUpload("src", "dest"),
                   transfer.FileDownload("src", "dest"),
                   ):
            try:
                self._loop(s)
            except:
                print "error checking %s" % s
                raise

