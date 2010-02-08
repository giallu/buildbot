# -*- test-case-name: buildbot.test.test_run -*-

from twisted.trial import unittest
from twisted.internet import reactor, defer
import os

from buildbot import master, interfaces
from buildbot.sourcestamp import SourceStamp
from buildbot.changes import changes
from buildbot.status import builder
#from buildbot.process.base import BuildRequest
from buildbot.process.builder import IDLE, BUILDING
from buildbot.eventual import flushEventualQueue

from buildbot.test.runutils import RunMixin, MasterMixin, TestFlagMixin, rmtree, StallMixin
from buildbot.test.pollmixin import PollMixin

config_base = """
from buildbot.process import factory
from buildbot.steps import dummy
from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
s = factory.s

f1 = factory.QuickBuildFactory('fakerep', 'cvsmodule', configure=None)

f2 = factory.BuildFactory([
    dummy.Dummy(timeout=1),
    dummy.RemoteDummy(timeout=2),
    ])

f3 = factory.BuildFactory([dummy.Wait('f3')])
f4 = factory.BuildFactory([dummy.Wait('f4')])

BuildmasterConfig = c = {}
c['slaves'] = [BuildSlave('bot1', 'sekrit')]
c['schedulers'] = []
c['builders'] = [
    BuilderConfig(name='quick', slavename='bot1', factory=f1,
        builddir='quickdir', slavebuilddir='slavequickdir'),
]
c['slavePortnum'] = 0
"""

config_run = config_base + """
from buildbot.scheduler import Scheduler
c['schedulers'] = [Scheduler('quick', None, 120, ['quick'])]
"""

config_can_build = config_base + """
from buildbot.buildslave import BuildSlave
c['slaves'] = [ BuildSlave('bot1', 'sekrit') ]

from buildbot.scheduler import Scheduler
c['schedulers'] = [Scheduler('dummy', None, None, ['dummy'])]

c['builders'] = [
    BuilderConfig(name='dummy', slavename='bot1',
                  factory=f3, builddir='dummy1'),
]
"""

config_cant_build = config_can_build + """
class MyBuildSlave(BuildSlave):
    def canStartBuild(self): return False
c['slaves'] = [ MyBuildSlave('bot1', 'sekrit') ]
"""

config_concurrency = config_base + """
from buildbot.buildslave import BuildSlave
c['slaves'] = [ BuildSlave('bot1', 'sekrit', max_builds=1) ]

from buildbot.scheduler import Scheduler
c['schedulers'] = [Scheduler('dummy', None, None, ['dummy', 'dummy2'])]

c['builders'] = c['builders'] + [
    BuilderConfig(name='dummy', slavename='bot1', factory=f3),
    BuilderConfig(name='dummy2', slavename='bot1', factory=f4),
]
"""

config_2 = config_base + """
c['builders'] = [
    BuilderConfig(name='dummy', slavename='bot1',
                  builddir='dummy1', factory=f2),
    BuilderConfig(name='test dummy', slavename='bot1',
                  factory=f2, category='test'),
]
"""

config_3 = config_2 + """
c['builders'] = c['builders'] + [
    BuilderConfig(name='adummy', slavename='bot1',
                  builddir='adummy3', factory=f2),
    BuilderConfig(name='bdummy', slavename='bot1',
                  builddir='adummy4', factory=f2,
                  category='test'),
]
"""

config_4 = config_base + """
c['builders'] = [
    BuilderConfig(name='dummy', slavename='bot1',
                  slavebuilddir='sdummy', factory=f2),
]
"""

config_4_newbasedir = config_4 + """
c['builders'] = [
    BuilderConfig(name='dummy', slavename='bot1',
                  builddir='dummy2', factory=f2),
]
"""

config_4_newbuilder = config_4_newbasedir + """
c['builders'] = c['builders'] + [
    BuilderConfig(name='dummy2', slavename='bot1',
                  builddir='dummy23', factory=f2),
]
"""

class Run(MasterMixin, StallMixin, unittest.TestCase):
    def testMaster(self):
        self.basedir = "run/Run/Master"
        self.create_master()
        m = self.master
        d = m.loadConfig(config_run)
        def _then(ign):
            cm = m.change_svc
            c = changes.Change("bob", ["Makefile", "foo/bar.c"],
                               "changed stuff")
            cm.addChange(c)
            return flushEventualQueue()
        d.addCallback(_then)
        d.addCallback(self.stall, 1.0) # needs time to finish. TODO: why is
                                       # flushEventualQueue not sufficient?
        # we used to check that the Scheduler is now waiting, but when its
        # state moved into the database, that became a nuisance
        def _check(ign):
            # verify that the Scheduler is now waiting
            s = m.allSchedulers()[0]
            sid = s.schedulerid
            state = m.db.runInteractionNow(lambda t:
                                         m.db.scheduler_get_state(sid, t))
            self.failUnlessEqual(state["last_processed"], 1)
        d.addCallback(_check)
        return d

from buildbot.slave.commands import waitCommandRegistry

class WaitMixin:
    # to use this, put a steps.dummy.Wait(handle="XYZ") in your test build
    # factory, prepare, then start the build with reason="ABC". Then use
    # something like this:
    #
    #  self.prepare_wait("XYZ", "ABC") # must be done before build starts
    #  d = run_one_build(self.control, buildername, ss, reason="ABC")
    #  d.addCallback(self.wait_until_step_reached, "XYZ", "ABC")
    #  def assert_is_in_step(ign):
    #      # now the build will be in the middle of the Wait step
    #      # you must release it to continue
    #      return self.release_build("XYZ", "ABC")
    #  d.addCallback(assert_is_in_step)
    #  d.addCallback(wait_for_build_to_finish)
    _wait_handles = None

    def prepare_wait(self, handle, reason):
        if self._wait_handles is None:
            self._wait_handles = {}
        h = (handle, reason)
        d1, d2 = defer.Deferred(), defer.Deferred()
        self._wait_handles[h] = (d1, d2)
        from buildbot.slave.commands import waitCommandRegistry
        def _catch():
            d1.callback(None)
            return d2
        waitCommandRegistry[h] = _catch
    def wait_until_step_reached(self, ign, handle, reason):
        h = (handle, reason)
        d1, d2 = self._wait_handles[h]
        return d1
    def release_build(self, res, handle, reason):
        h = (handle, reason)
        d1, d2 = self._wait_handles[h]
        del self._wait_handles[h]
        d2.callback(None)
        return res
    def release_all_builds(self, res=None):
        for (d1,d2) in self._wait_handles.values():
            d2.callback(None)
        del self._wait_handles
        return res

class CanStartBuild(MasterMixin, StallMixin, WaitMixin, unittest.TestCase):
    def testCanStartBuild(self):
        self.basedir = "run/CanStartBuild/CanStartBuild"
        self.prepare_wait("f3", "scheduler")
        d = self.do_test(config_can_build)
        d.addCallback(self.wait_until_step_reached, "f3", "scheduler")
        d.addCallback(self._check, BUILDING)
        d.addCallback(self.release_build, "f3", "scheduler")
        d.addCallback(lambda bs: bs.waitUntilFinished())
        return d

    def testCantStartBuild(self):
        self.basedir = "run/CanStartBuild/CantStartBuild"
        d = self.do_test(config_cant_build)
        d.addCallback(self._check, IDLE)
        return d

    def do_test(self, config):
        self.create_master()
        d = self.master.loadConfig(config)
        d.addCallback(lambda ign: self.connectSlave())
        def _then(ign):
            # send a change
            cm = self.master.change_svc
            c = changes.Change("bob", ["Makefile", "foo/bar.c"],
                               "changed stuff")
            cm.addChange(c)
        d.addCallback(_then)
        return d

    def _check(self, ign, expected_state):
        b = self.master.botmaster.builders['dummy']
        self.failUnless(len(b.slaves) == 1)
        buildslave = b.slaves[0]
        self.failUnlessEqual(buildslave.state, expected_state)
        if buildslave.state == BUILDING:
            bs = self.master.status.getBuilder("dummy").getCurrentBuilds()[0]
            return bs
        return None


class FirstToFire(defer.Deferred):
    def __init__(self, *ds):
        defer.Deferred.__init__(self)
        # self.called
        for d in ds:
            d.addBoth(self._one_fired)
    def _one_fired(self, res):
        if not self.called:
            self.callback(res)
        return res

class ConcurrencyLimit(MasterMixin, WaitMixin, StallMixin, PollMixin,
                       unittest.TestCase):

    def testConcurrencyLimit(self):
        self.basedir = "run/ConcurrencyLimit/ConcurrencyLimit"
        self.create_master()
        self.prepare_wait("f3", "scheduler")
        self.prepare_wait("f4", "scheduler")
        d = self.master.loadConfig(config_concurrency)
        d.addCallback(lambda res: self.connectSlave())

        def _send(res):
            # send a change. This will trigger both builders at the same
            # time, but since they share a slave, the max_builds=1 setting
            # will insure that only one of the two builds gets to run.
            # TODO: setting max_builds=2 doesn't cause this to fail.
            # TODO: sometimes this errors out
            cm = self.master.change_svc
            c = changes.Change("bob", ["Makefile", "foo/bar.c"],
                               "changed stuff")
            cm.addChange(c)
            d1 = self.wait_until_step_reached(None, "f3", "scheduler")
            d2 = self.wait_until_step_reached(None, "f4", "scheduler")
            return FirstToFire(d1, d2)
        d.addCallback(_send)

        def _check(res):
            builders = [ self.master.botmaster.builders[bn]
                         for bn in ('dummy', 'dummy2') ]
            for builder in builders:
                self.failUnless(len(builder.slaves) == 1)

            building_bs = [ builder
                            for builder in builders
                            if builder.slaves[0].state == BUILDING ]
            # assert that only one build is running right now. If the
            # max_builds= weren't in effect, this would be 2.
            self.failUnlessEqual(len(building_bs), 1)
            self.release_all_builds()
        d.addCallback(_check)
        # this _wait approach sometimes fails, leaving a timer lying around
        # which fires after the master has shut down (causing an error). I
        # don't know why. I had to punt and add the stall().
        def _wait():
            s = self.master.status
            if s.getBuilder("dummy").getLastFinishedBuild() is None:
                return False
            if s.getBuilder("dummy2").getLastFinishedBuild() is None:
                return False
            return True
        d.addCallback(lambda ign: self.poll(_wait))
        d.addCallback(self.stall, 0.5)
        return d


class Ping(MasterMixin, unittest.TestCase):
    def testPing(self):
        self.basedir = "run/Ping/Ping"
        self.create_master()
        c = interfaces.IControl(self.master)
        d = self.master.loadConfig(config_2)
        d.addCallback(lambda ign: self.connectSlave())
        d.addCallback(lambda ign: c.getBuilder("dummy").ping())
        return d

class BuilderNames(MasterMixin, unittest.TestCase):

    def testGetBuilderNames(self):
        self.basedir = "run/BuilderNames/GetBuilderNames"
        self.create_master()
        s = self.master.getStatus()
        d = self.master.loadConfig(config_3)
        def _check(ign):
            self.failUnlessEqual(s.getBuilderNames(),
                                 ["dummy", "test dummy", "adummy", "bdummy"])
            self.failUnlessEqual(s.getBuilderNames(categories=['test']),
                                 ["test dummy", "bdummy"])
        d.addCallback(_check)
        return d

class Disconnect(MasterMixin, StallMixin, PollMixin, unittest.TestCase):
    # verify that disconnecting the slave during a build properly
    # terminates the build

    def startup(self):
        m = self.master
        d = self.master.loadConfig(config_2)
        d.addCallback(self._earlycheck)
        d.addCallback(lambda ign: self.connectSlave())
        d.addCallback(self.stall, 0.5)
        d.addCallback(self._earlycheck2)
        return d

    def _earlycheck(self, ign):
        s = self.status
        self.failUnlessEqual(s.getBuilderNames(), ["dummy", "test dummy"])
        self.s1 = s1 = s.getBuilder("dummy")
        self.failUnlessEqual(s1.getName(), "dummy")
        self.failUnlessEqual(s1.getState(), ("offline", []))
        self.failUnlessEqual(s1.getCurrentBuilds(), [])
        self.failUnlessEqual(s1.getLastFinishedBuild(), None)
        self.failUnlessEqual(s1.getBuild(-1), None)

    def _earlycheck2(self, res):
        self.failUnlessEqual(self.s1.getState(), ("idle", []))

    def test_null(self):
        self.basedir = "run/Disconnect/null"
        self.create_master()
        d = self.startup()
        return d

    def verifyDisconnect(self, bs):
        self.failUnless(bs.isFinished())

        step1 = bs.getSteps()[0]
        self.failUnlessEqual(step1.getText(), ["delay", "interrupted"])
        self.failUnlessEqual(step1.getResults()[0], builder.FAILURE)

        self.failUnlessEqual(bs.getResults(), builder.FAILURE)

    def verifyDisconnect2(self, bs):
        self.failUnless(bs.isFinished())

        step1 = bs.getSteps()[1]
        self.failUnlessEqual(step1.getText(), ["remote", "delay", "2 secs",
                                               "failed", "slave", "lost"])
        self.failUnlessEqual(step1.getResults()[0], builder.FAILURE)

        self.failUnlessEqual(bs.getResults(), builder.FAILURE)

    def submitBuild(self):
        ss = SourceStamp()
        bss = self.control.submitBuildSet(["dummy"], ss, "forced build")
        brs = bss.getBuildRequests()[0]
        d = defer.Deferred()
        def _started(bc):
            brs.unsubscribe(_started)
            d.callback(bc)
        brs.subscribe(_started)
        return d

    def testIdle(self):
        # if the slave does not respond to a ping when the build starts, the
        # build will be put back in the queue (i.e. unclaimed)
        self.basedir = "run/Disconnect/idle"
        self.create_master()
        db = self.master.db
        d = self.startup()
        def _then(ign):
            self.disappearSlave(allowReconnect=False)
            # the slave is now frozen, and won't respond to a ping. We submit
            # a build request, and wait for it to be claimed (shouldn't take
            # long). The build should be stuck in the waiting-for-pong state.
            ss = SourceStamp()
            bss = self.control.submitBuildSet(["dummy"], ss, "forced build")
            brs = bss.getBuildRequests()[0]
            self.brid = brs.brid
        d.addCallback(_then)
        def _get_claim(ign=None):
            def _db(t):
                t.execute(db.quoteq("SELECT claimed_at FROM buildrequests"
                                    " WHERE id=?"),
                          (self.brid,))
                return t.fetchall()[0][0]
            claimed_at = db.runInteractionNow(_db)
            return claimed_at
        def _is_claimed():
            return bool(_get_claim() > 0)
        d.addCallback(lambda ign: self.poll(_is_claimed))
        # now we kill the slave, which drops the TCP connection and halts the
        # ping. The BuildRequest should be put back into the unclaimed state.
        d.addCallback(lambda ign: self.killSlave())
        d.addCallback(self.wait_until_idle)
        d.addCallback(_get_claim)
        d.addCallback(lambda claim: self.failUnlessEqual(claim, 0))
        return d
    testIdle.timeout = 10


    def testBuild1(self):
        # this next sequence is timing-dependent. The dummy build takes at
        # least 3 seconds to complete, and this batch of commands must
        # complete within that time.
        #
        d = self.submitBuild()
        d.addCallback(self._testBuild1_1)
        return d

    def _testBuild1_1(self, bc):
        bs = bc.getStatus()
        # now kill the slave before it gets to start the first step
        d = self.shutdownAllSlaves() # dies before it gets started
        d.addCallback(self._testBuild1_2, bs)
        return d  # TODO: this used to have a 5-second timeout

    def _testBuild1_2(self, res, bs):
        # now examine the just-stopped build and make sure it is really
        # stopped. This is checking for bugs in which the slave-detach gets
        # missed or causes an exception which prevents the build from being
        # marked as "finished due to an error".
        d = bs.waitUntilFinished()
        d2 = self.master.botmaster.waitUntilBuilderDetached("dummy")
        dl = defer.DeferredList([d, d2])
        dl.addCallback(self._testBuild1_3, bs)
        return dl # TODO: this had a 5-second timeout too

    def _testBuild1_3(self, res, bs):
        self.failUnlessEqual(self.s1.getState()[0], "offline")
        self.verifyDisconnect(bs)


    def testBuild2(self):
        # this next sequence is timing-dependent
        d = self.submitBuild()
        d.addCallback(self._testBuild2_1)
        return d
    testBuild2.timeout = 30

    def _testBuild2_1(self, bc):
        bs = bc.getStatus()
        # shutdown the slave while it's running the first step
        reactor.callLater(0.5, self.shutdownAllSlaves)

        d = bs.waitUntilFinished()
        d.addCallback(self._testBuild2_2, bs)
        return d

    def _testBuild2_2(self, res, bs):
        # we hit here when the build has finished. The builder is still being
        # torn down, however, so spin for another second to allow the
        # callLater(0) in Builder.detached to fire.
        d = defer.Deferred()
        reactor.callLater(1, d.callback, None)
        d.addCallback(self._testBuild2_3, bs)
        return d

    def _testBuild2_3(self, res, bs):
        self.failUnlessEqual(self.s1.getState()[0], "offline")
        self.verifyDisconnect(bs)


    def testBuild3(self):
        # this next sequence is timing-dependent
        d = self.submitBuild()
        d.addCallback(self._testBuild3_1)
        return d
    testBuild3.timeout = 30

    def _testBuild3_1(self, bc):
        bs = bc.getStatus()
        # kill the slave while it's running the first step
        reactor.callLater(0.5, self.killSlave)
        d = bs.waitUntilFinished()
        d.addCallback(self._testBuild3_2, bs)
        return d

    def _testBuild3_2(self, res, bs):
        # the builder is still being torn down, so give it another second
        d = defer.Deferred()
        reactor.callLater(1, d.callback, None)
        d.addCallback(self._testBuild3_3, bs)
        return d

    def _testBuild3_3(self, res, bs):
        self.failUnlessEqual(self.s1.getState()[0], "offline")
        self.verifyDisconnect(bs)


    def testBuild4(self):
        # this next sequence is timing-dependent
        d = self.submitBuild()
        d.addCallback(self._testBuild4_1)
        return d
    testBuild4.timeout = 30

    def _testBuild4_1(self, bc):
        bs = bc.getStatus()
        # kill the slave while it's running the second (remote) step
        reactor.callLater(1.5, self.killSlave)
        d = bs.waitUntilFinished()
        d.addCallback(self._testBuild4_2, bs)
        return d

    def _testBuild4_2(self, res, bs):
        # at this point, the slave is in the process of being removed, so it
        # could either be 'idle' or 'offline'. I think there is a
        # reactor.callLater(0) standing between here and the offline state.
        #reactor.iterate() # TODO: remove the need for this

        self.failUnlessEqual(self.s1.getState()[0], "offline")
        self.verifyDisconnect2(bs)


    def testInterrupt(self):
        # this next sequence is timing-dependent
        d = self.submitBuild()
        d.addCallback(self._testInterrupt_1)
        return d
    testInterrupt.timeout = 30

    def _testInterrupt_1(self, bc):
        bs = bc.getStatus()
        # halt the build while it's running the first step
        reactor.callLater(0.5, bc.stopBuild, "bang go splat")
        d = bs.waitUntilFinished()
        d.addCallback(self._testInterrupt_2, bs)
        return d

    def _testInterrupt_2(self, res, bs):
        self.verifyDisconnect(bs)


    def testDisappear(self):
        bc = self.control.getBuilder("dummy")

        # ping should succeed
        d = bc.ping()
        d.addCallback(self._testDisappear_1, bc)
        return d

    def _testDisappear_1(self, res, bc):
        self.failUnlessEqual(res, True)

        # now, before any build is run, make the slave disappear
        self.disappearSlave(allowReconnect=False)

        # initiate the ping and then kill the slave, to simulate a disconnect.
        d = bc.ping()
        self.killSlave()
        d.addCallback(self. _testDisappear_2)
        return d
    def _testDisappear_2(self, res):
        self.failUnlessEqual(res, False)

    def testDuplicate(self):
        bc = self.control.getBuilder("dummy")
        bs = self.status.getBuilder("dummy")
        ss = bs.getSlaves()[0]

        self.failUnless(ss.isConnected())
        self.failUnlessEqual(ss.getAdmin(), "one")

        # now, before any build is run, make the first slave disappear
        self.disappearSlave(allowReconnect=False)

        d = self.master.botmaster.waitUntilBuilderDetached("dummy")
        # now let the new slave take over
        self.connectSlave2()
        d.addCallback(self._testDuplicate_1, ss)
        return d
    testDuplicate.timeout = 5

    def _testDuplicate_1(self, res, ss):
        d = self.master.botmaster.waitUntilBuilderAttached("dummy")
        d.addCallback(self._testDuplicate_2, ss)
        return d

    def _testDuplicate_2(self, res, ss):
        self.failUnless(ss.isConnected())
        self.failUnlessEqual(ss.getAdmin(), "two")


class Disconnect2(RunMixin, unittest.TestCase):

    def setUp(self):
        RunMixin.setUp(self)
        # verify that disconnecting the slave during a build properly
        # terminates the build
        m = self.master
        s = self.status
        c = self.control

        m.loadConfig(config_2)
        m.readConfig = True
        m.startService()

        self.failUnlessEqual(s.getBuilderNames(), ["dummy", "test dummy"])
        self.s1 = s1 = s.getBuilder("dummy")
        self.failUnlessEqual(s1.getName(), "dummy")
        self.failUnlessEqual(s1.getState(), ("offline", []))
        self.failUnlessEqual(s1.getCurrentBuilds(), [])
        self.failUnlessEqual(s1.getLastFinishedBuild(), None)
        self.failUnlessEqual(s1.getBuild(-1), None)

        d = self.connectSlaveFastTimeout()
        d.addCallback(self._setup_disconnect2_1)
        return d

    def _setup_disconnect2_1(self, res):
        self.failUnlessEqual(self.s1.getState(), ("idle", []))


    def testSlaveTimeout(self):
        # now suppose the slave goes missing. We want to find out when it
        # creates a new Broker, so we reach inside and mark it with the
        # well-known sigil of impending messy death.
        bd = self.slaves['bot1'].getServiceNamed("bot").builders["dummy"]
        broker = bd.remote.broker
        broker.redshirt = 1

        # make sure the keepalives will keep the connection up
        d = defer.Deferred()
        reactor.callLater(5, d.callback, None)
        d.addCallback(self._testSlaveTimeout_1)
        return d
    testSlaveTimeout.timeout = 20

    def _testSlaveTimeout_1(self, res):
        bd = self.slaves['bot1'].getServiceNamed("bot").builders["dummy"]
        if not bd.remote or not hasattr(bd.remote.broker, "redshirt"):
            self.fail("slave disconnected when it shouldn't have")

        d = self.master.botmaster.waitUntilBuilderDetached("dummy")
        # whoops! how careless of me.
        self.disappearSlave(allowReconnect=True)
        # the slave will realize the connection is lost within 2 seconds, and
        # reconnect.
        d.addCallback(self._testSlaveTimeout_2)
        return d

    def _testSlaveTimeout_2(self, res):
        # the ReconnectingPBClientFactory will attempt a reconnect in two
        # seconds.
        d = self.master.botmaster.waitUntilBuilderAttached("dummy")
        d.addCallback(self._testSlaveTimeout_3)
        return d

    def _testSlaveTimeout_3(self, res):
        # make sure it is a new connection (i.e. a new Broker)
        bd = self.slaves['bot1'].getServiceNamed("bot").builders["dummy"]
        self.failUnless(bd.remote, "hey, slave isn't really connected")
        self.failIf(hasattr(bd.remote.broker, "redshirt"),
                    "hey, slave's Broker is still marked for death")


class Basedir(MasterMixin, unittest.TestCase):
    def testChangeBuilddir(self):
        self.basedir = "run/Basedir/ChangeBuilddir"
        self.create_master()
        m = self.master
        d = m.loadConfig(config_4)
        d.addCallback(lambda ign: self.connectSlave())
        d.addCallback(self._testChangeBuilddir_1)
        return d

    def _testChangeBuilddir_1(self, res):
        self.bot = bot = self.slaves['bot1'].bot
        self.builder = builder = bot.builders.get("dummy")
        self.failUnless(builder)
        # slavebuilddir value.
        self.failUnlessEqual(builder.builddir, "sdummy")
        self.failUnlessEqual(builder.basedir,
                             os.path.join(self.basedir, "slavebase-bot1", "sdummy"))

        d = self.master.loadConfig(config_4_newbasedir)
        d.addCallback(self._testChangeBuilddir_2)
        return d

    def _testChangeBuilddir_2(self, res):
        bot = self.bot
        # this does NOT cause the builder to be replaced
        builder = bot.builders.get("dummy")
        self.failUnless(builder)
        self.failUnlessIdentical(self.builder, builder)
        # the basedir should be updated
        self.failUnlessEqual(builder.builddir, "dummy2")
        self.failUnlessEqual(builder.basedir,
                             os.path.join(self.basedir, "slavebase-bot1", "dummy2"))

        # add a new builder, which causes the basedir list to be reloaded
        d = self.master.loadConfig(config_4_newbuilder)
        return d

class Triggers(RunMixin, TestFlagMixin, unittest.TestCase):
    config_trigger = config_base + """
from buildbot.scheduler import Triggerable, Scheduler
from buildbot.steps.trigger import Trigger
from buildbot.steps.dummy import Dummy
from buildbot.test.runutils import SetTestFlagStep
from buildbot.process.properties import WithProperties
c['schedulers'] = [
    Scheduler('triggerer', None, 0.1, ['triggerer'], properties={'dyn':'dyn'}),
    Triggerable('triggeree', ['triggeree'])
]
triggerer = factory.BuildFactory()
triggerer.addSteps([
    SetTestFlagStep(flagname='triggerer_started'),
    Trigger(flunkOnFailure=True, @ARGS@),
    SetTestFlagStep(flagname='triggerer_finished'),
    ])
triggeree = factory.BuildFactory([
    s(SetTestFlagStep, flagname='triggeree_started'),
    s(@DUMMYCLASS@),
    s(SetTestFlagStep, flagname='triggeree_finished'),
    ])
c['builders'] = [{'name': 'triggerer', 'slavename': 'bot1',
                  'builddir': 'triggerer', 'factory': triggerer},
                 {'name': 'triggeree', 'slavename': 'bot1',
                  'builddir': 'triggeree', 'factory': triggeree}]
"""

    def mkConfig(self, args, dummyclass="Dummy"):
        return self.config_trigger.replace("@ARGS@", args).replace("@DUMMYCLASS@", dummyclass)

    def setupTest(self, args, dummyclass, checkFn):
        self.clearFlags()
        m = self.master
        m.loadConfig(self.mkConfig(args, dummyclass))
        m.readConfig = True
        m.startService()

        c = changes.Change("bob", ["Makefile", "foo/bar.c"], "changed stuff")
        m.change_svc.addChange(c)

        d = self.connectSlave(builders=['triggerer', 'triggeree'])
        d.addCallback(self.startTimer, 0.5, checkFn)
        return d

    def startTimer(self, res, time, next_fn):
        d = defer.Deferred()
        reactor.callLater(time, d.callback, None)
        d.addCallback(next_fn)
        return d

    def testTriggerBuild(self):
        return self.setupTest("schedulerNames=['triggeree']",
                "Dummy",
                self._checkTriggerBuild)

    def _checkTriggerBuild(self, res):
        self.failIfFlagNotSet('triggerer_started')
        self.failIfFlagNotSet('triggeree_started')
        self.failIfFlagSet('triggeree_finished')
        self.failIfFlagNotSet('triggerer_finished')

    def testTriggerBuildWait(self):
        return self.setupTest("schedulerNames=['triggeree'], waitForFinish=1",
                "Dummy",
                self._checkTriggerBuildWait)

    def _checkTriggerBuildWait(self, res):
        self.failIfFlagNotSet('triggerer_started')
        self.failIfFlagNotSet('triggeree_started')
        self.failIfFlagSet('triggeree_finished')
        self.failIfFlagSet('triggerer_finished')

    def testProperties(self):
        return self.setupTest("""
                schedulerNames=['triggeree'],
                set_properties={'lit' : 'lit'},
                copy_properties=['dyn']
            """, """
                SetTestFlagStep, flagname='props',
                    value=WithProperties('%(lit:-MISSING)s:%(dyn:-MISSING)s')
            """,
                self._checkProperties)

    def _checkProperties(self, res):
        self.assertEqual(self.getFlag("props"), "lit:dyn")

class PropertyPropagation(RunMixin, TestFlagMixin, unittest.TestCase):
    def setupTest(self, config, builders, checkFn, changeProps={}):
        self.clearFlags()
        m = self.master
        m.loadConfig(config)
        m.readConfig = True
        m.startService()

        c = changes.Change("bob", ["Makefile", "foo/bar.c"], "changed stuff",
                           properties=changeProps)
        m.change_svc.addChange(c)

        d = self.connectSlave(builders=builders)
        d.addCallback(self.startTimer, 0.5, checkFn)
        return d

    def startTimer(self, res, time, next_fn):
        d = defer.Deferred()
        reactor.callLater(time, d.callback, None)
        d.addCallback(next_fn)
        return d

    config_schprop = config_base + """
from buildbot.scheduler import Scheduler
from buildbot.steps.dummy import Dummy
from buildbot.test.runutils import SetTestFlagStep
from buildbot.process.properties import WithProperties
c['schedulers'] = [
    Scheduler('mysched', None, 0.1, ['flagcolor'], properties={'color':'red'}),
]
factory = factory.BuildFactory([
    s(SetTestFlagStep, flagname='testresult', 
      value=WithProperties('color=%(color)s sched=%(scheduler)s')),
    ])
c['builders'] = [{'name': 'flagcolor', 'slavename': 'bot1',
                  'builddir': 'test', 'factory': factory},
                ]
"""

    def testScheduler(self):
        def _check(res):
            self.failUnlessEqual(self.getFlag('testresult'),
                'color=red sched=mysched')
        return self.setupTest(self.config_schprop, ['flagcolor'], _check)

    config_changeprop = config_base + """
from buildbot.scheduler import Scheduler
from buildbot.steps.dummy import Dummy
from buildbot.test.runutils import SetTestFlagStep
from buildbot.process.properties import WithProperties
c['schedulers'] = [
    Scheduler('mysched', None, 0.1, ['flagcolor'], properties={'color':'red'}),
]
factory = factory.BuildFactory([
    s(SetTestFlagStep, flagname='testresult', 
      value=WithProperties('color=%(color)s sched=%(scheduler)s prop1=%(prop1)s')),
    ])
c['builders'] = [{'name': 'flagcolor', 'slavename': 'bot1',
                  'builddir': 'test', 'factory': factory},
                ]
"""

    def testChangeProp(self):
        def _check(res):
            self.failUnlessEqual(self.getFlag('testresult'),
                'color=blue sched=mysched prop1=prop1')
        return self.setupTest(self.config_changeprop, ['flagcolor'], _check,
                              changeProps={'color': 'blue', 'prop1': 'prop1'})

    config_slaveprop = config_base + """
from buildbot.scheduler import Scheduler
from buildbot.steps.dummy import Dummy
from buildbot.test.runutils import SetTestFlagStep
from buildbot.process.properties import WithProperties
c['schedulers'] = [
    Scheduler('mysched', None, 0.1, ['flagcolor'])
]
c['slaves'] = [BuildSlave('bot1', 'sekrit', properties={'color':'orange'})]
factory = factory.BuildFactory([
    s(SetTestFlagStep, flagname='testresult', 
      value=WithProperties('color=%(color)s slavename=%(slavename)s')),
    ])
c['builders'] = [{'name': 'flagcolor', 'slavename': 'bot1',
                  'builddir': 'test', 'factory': factory},
                ]
"""
    def testSlave(self):
        def _check(res):
            self.failUnlessEqual(self.getFlag('testresult'),
                'color=orange slavename=bot1')
        return self.setupTest(self.config_slaveprop, ['flagcolor'], _check)

    config_trigger = config_base + """
from buildbot.scheduler import Triggerable, Scheduler
from buildbot.steps.trigger import Trigger
from buildbot.steps.dummy import Dummy
from buildbot.test.runutils import SetTestFlagStep
from buildbot.process.properties import WithProperties
c['schedulers'] = [
    Scheduler('triggerer', None, 0.1, ['triggerer'], 
        properties={'color':'mauve', 'pls_trigger':'triggeree'}),
    Triggerable('triggeree', ['triggeree'], properties={'color':'invisible'})
]
triggerer = factory.BuildFactory([
    s(SetTestFlagStep, flagname='testresult', value='wrongone'),
    s(Trigger, flunkOnFailure=True, 
        schedulerNames=[WithProperties('%(pls_trigger)s')],
        set_properties={'color' : WithProperties('%(color)s')}),
    s(SetTestFlagStep, flagname='testresult', value='triggered'),
    ])
triggeree = factory.BuildFactory([
    s(SetTestFlagStep, flagname='testresult', 
        value=WithProperties('sched=%(scheduler)s color=%(color)s')),
    ])
c['builders'] = [{'name': 'triggerer', 'slavename': 'bot1',
                  'builddir': 'triggerer', 'factory': triggerer},
                 {'name': 'triggeree', 'slavename': 'bot1',
                  'builddir': 'triggeree', 'factory': triggeree}]
"""
    def testTrigger(self):
        def _check(res):
            self.failUnlessEqual(self.getFlag('testresult'),
                'sched=triggeree color=mauve')
        return self.setupTest(self.config_trigger, 
                ['triggerer', 'triggeree'], _check)


config_test_flag = config_base + """
from buildbot.scheduler import Scheduler
c['schedulers'] = [Scheduler('quick', None, 0.1, ['dummy'])]

from buildbot.test.runutils import SetTestFlagStep
f3 = factory.BuildFactory([
    s(SetTestFlagStep, flagname='foo', value='bar'),
    ])

c['builders'] = [{'name': 'dummy', 'slavename': 'bot1',
                  'builddir': 'dummy', 'factory': f3}]
"""

class TestFlag(RunMixin, TestFlagMixin, unittest.TestCase):
    """Test for the TestFlag functionality in runutils"""
    def testTestFlag(self):
        m = self.master
        m.loadConfig(config_test_flag)
        m.readConfig = True
        m.startService()

        c = changes.Change("bob", ["Makefile", "foo/bar.c"], "changed stuff")
        m.change_svc.addChange(c)

        d = self.connectSlave()
        d.addCallback(self._testTestFlag_1)
        return d

    def _testTestFlag_1(self, res):
        d = defer.Deferred()
        reactor.callLater(0.5, d.callback, None)
        d.addCallback(self._testTestFlag_2)
        return d

    def _testTestFlag_2(self, res):
        self.failUnlessEqual(self.getFlag('foo'), 'bar')

# TODO: test everything, from Change submission to Scheduler to Build to
# Status. Use all the status types. Specifically I want to catch recurrences
# of the bug where I forgot to make Waterfall inherit from StatusReceiver
# such that buildSetSubmitted failed.

config_test_builder = config_base + """
from buildbot.scheduler import Scheduler
c['schedulers'] = [Scheduler('quick', 'dummy', 0.1, ['dummy']),
                   Scheduler('quick2', 'dummy2', 0.1, ['dummy2']),
                   Scheduler('quick3', 'dummy3', 0.1, ['dummy3'])]

from buildbot.steps.shell import ShellCommand
f3 = factory.BuildFactory([
    s(ShellCommand, command="sleep 3", env={'blah':'blah'})
    ])

c['builders'] = [{'name': 'dummy', 'slavename': 'bot1', 'env': {'foo':'bar'},
                  'builddir': 'dummy', 'factory': f3}]

c['builders'].append({'name': 'dummy2', 'slavename': 'bot1',
                       'env': {'blah':'bar'}, 'builddir': 'dummy2',
                       'factory': f3})

f4 = factory.BuildFactory([
    s(ShellCommand, command="sleep 3")
    ])

c['builders'].append({'name': 'dummy3', 'slavename': 'bot1',
                       'env': {'blah':'bar'}, 'builddir': 'dummy3',
                       'factory': f4})
"""

class TestBuilder(RunMixin, unittest.TestCase):
    def setUp(self):
        RunMixin.setUp(self)
        self.master.loadConfig(config_test_builder)
        self.master.readConfig = True
        self.master.startService()
        self.connectSlave(builders=["dummy", "dummy2", "dummy3"])

    def doBuilderEnvTest(self, branch, cb):
        c = changes.Change("bob", ["Makefile", "foo/bar.c"], "changed",
                           branch=branch)
        self.master.change_svc.addChange(c)

        d = defer.Deferred()
        reactor.callLater(0.5, d.callback, None)
        d.addCallback(cb)

        return d

    def testBuilderEnv(self):
        return self.doBuilderEnvTest("dummy", self._testBuilderEnv1)

    def _testBuilderEnv1(self, res):
        b = self.master.botmaster.builders['dummy']
        build = b.building[0]
        s = build.currentStep
        self.failUnless('foo' in s.cmd.args['env'])
        self.failUnlessEqual('bar', s.cmd.args['env']['foo'])
        self.failUnless('blah' in s.cmd.args['env'])
        self.failUnlessEqual('blah', s.cmd.args['env']['blah'])

    def testBuilderEnvOverride(self):
        return self.doBuilderEnvTest("dummy2", self._testBuilderEnvOverride1)

    def _testBuilderEnvOverride1(self, res):
        b = self.master.botmaster.builders['dummy2']
        build = b.building[0]
        s = build.currentStep
        self.failUnless('blah' in s.cmd.args['env'])
        self.failUnlessEqual('blah', s.cmd.args['env']['blah'])

    def testBuilderNoStepEnv(self):
        return self.doBuilderEnvTest("dummy3", self._testBuilderNoStepEnv1)

    def _testBuilderNoStepEnv1(self, res):
        b = self.master.botmaster.builders['dummy3']
        build = b.building[0]
        s = build.currentStep
        self.failUnless('blah' in s.cmd.args['env'])
        self.failUnlessEqual('bar', s.cmd.args['env']['blah'])

class SchedulerWatchers(RunMixin, TestFlagMixin, unittest.TestCase):
    config_watchable = config_base + """
from buildbot.scheduler import AnyBranchScheduler
from buildbot.steps.dummy import Dummy
from buildbot.test.runutils import setTestFlag, SetTestFlagStep
s = AnyBranchScheduler(
    name='abs',
    branches=None,
    treeStableTimer=0,
    builderNames=['a', 'b'])
c['schedulers'] = [ s ]

# count the number of times a success watcher is called
numCalls = [ 0 ]
def watcher(ss):
    numCalls[0] += 1
    setTestFlag("numCalls", numCalls[0])
s.subscribeToSuccessfulBuilds(watcher)

f = factory.BuildFactory()
f.addStep(Dummy(timeout=0))
c['builders'] = [{'name': 'a', 'slavename': 'bot1',
                  'builddir': 'a', 'factory': f},
                 {'name': 'b', 'slavename': 'bot1',
                  'builddir': 'b', 'factory': f}]
"""

    def testWatchers(self):
        self.clearFlags()
        m = self.master
        m.loadConfig(self.config_watchable)
        m.readConfig = True
        m.startService()

        c = changes.Change("bob", ["Makefile", "foo/bar.c"], "changed stuff")
        m.change_svc.addChange(c)

        d = self.connectSlave(builders=['a', 'b'])

        def pause(res):
            d = defer.Deferred()
            reactor.callLater(1, d.callback, res)
            return d
        d.addCallback(pause)

        def checkFn(res):
            self.failUnlessEqual(self.getFlag('numCalls'), 1)
        d.addCallback(checkFn)
        return d

config_priority = """
from buildbot.process import factory
from buildbot.steps import dummy
from buildbot.buildslave import BuildSlave
s = factory.s

from buildbot.steps.shell import ShellCommand
f1 = factory.BuildFactory([
    s(ShellCommand, command="sleep 1", env={'blah':'blah'})
    ])

BuildmasterConfig = c = {}
slavenames = ['bot%i' % i for i in range(5)]
c['slaves'] = [BuildSlave(name, 'sekrit', max_builds=1) for name in slavenames]
c['schedulers'] = []
c['builders'] = []
c['builders'].append({'name':'quick1', 'slavenames':slavenames, 'builddir': 'quickdir1', 'factory': f1})
c['builders'].append({'name':'quick2', 'slavenames':slavenames, 'builddir': 'quickdir2', 'factory': f1})
c['slavePortnum'] = 0
"""

class BuildPrioritization(RunMixin, unittest.TestCase):
    def rmtree(self, d):
        rmtree(d)

    def testPriority(self):
        self.rmtree("basedir")
        os.mkdir("basedir")
        self.master.loadConfig(config_priority)
        self.master.readConfig = True
        self.master.startService()

        # Our fake source stamp
        # we override canBeMergedWith so that our requests don't get merged together
        ss = SourceStamp()
        ss.canBeMergedWith = lambda x: False

        # Send 10 requests to alternating builders
        # We fudge the submittedAt field after submitting since they're all
        # getting submitted so close together according to time.time()
        # and all we care about is what order they're run in.
        reqs = []
        self.start_order = []
        for i in range(10):
            req = BuildRequest(str(i), ss, "test_builder")
            j = i % 2 + 1
            self.master.botmaster.builders['quick%i' % j].submitBuildRequest(req)
            req.submittedAt = i
            # Keep track of what order the builds start in
            def append(build):
                self.start_order.append(int(build.reason))
            req.subscribe(append)
            reqs.append(req.waitUntilFinished())

        dl = defer.DeferredList(reqs)
        dl.addCallback(self._all_finished)

        def _delay(res):
            d1 = defer.Deferred()
            reactor.callLater(0.5, d1.callback, None)
            # this test depends upon this 0.5s delay landing us in the middle
            # of one of the builds.
            return d1

        def _connect(res, i):
            return self.connectSlave(slavename="bot%i" % i, builders=["quick1", "quick2"])

        # Now add the slaves
        d = self.connectSlave(slavename="bot0", builders=["quick1", "quick2"])
        for i in range(1,5):
            d.addCallback(_delay)
            d.addCallback(_connect, i)

        d.addCallback(lambda x: dl)

        return d

    def _all_finished(self, *args):
        # The builds should have finished in proper order
        self.failUnlessEqual(self.start_order, range(10))

# Test graceful shutdown when no builds are active, as well as
# canStartBuild after graceful shutdown is initiated
config_graceful_shutdown_idle = config_base
class GracefulShutdownIdle(RunMixin, unittest.TestCase):
    def testShutdown(self):
        self.rmtree("basedir")
        os.mkdir("basedir")
        self.master.loadConfig(config_graceful_shutdown_idle)
        self.master.readConfig = True
        self.master.startService()
        d = self.connectSlave(builders=['quick'])
        d.addCallback(self._do_shutdown)
        return d

    def _do_shutdown(self, res):
        bs = self.master.botmaster.builders['quick'].slaves[0]
        # Check that the slave is accepting builds once it's connected
        self.assertEquals(bs.slave.canStartBuild(), True)

        # Monkeypatch the slave's shutdown routine since the real shutdown
        # interrupts the test harness
        self.did_shutdown = False
        def _shutdown():
            self.did_shutdown = True
        bs.slave.shutdown = _shutdown

        # Start a graceful shutdown
        bs.slave.slave_status.setGraceful(True)
        # Check that the slave isn't accepting builds any more
        self.assertEquals(bs.slave.canStartBuild(), False)

        # Wait a little bit and then check that we (pretended to) shut down
        d = defer.Deferred()
        d.addCallback(self._check_shutdown)
        reactor.callLater(0.5, d.callback, None)
        return d

    def _check_shutdown(self, res):
        self.assertEquals(self.did_shutdown, True)

# Test graceful shutdown when two builds are active
config_graceful_shutdown_busy = config_base + """
from buildbot.buildslave import BuildSlave
c['slaves'] = [ BuildSlave('bot1', 'sekrit', max_builds=2) ]

from buildbot.scheduler import Scheduler
c['schedulers'] = [Scheduler('dummy', None, 0.1, ['dummy', 'dummy2'])]

c['builders'].append({'name': 'dummy', 'slavename': 'bot1',
                      'builddir': 'dummy', 'factory': f2})
c['builders'].append({'name': 'dummy2', 'slavename': 'bot1',
                      'builddir': 'dummy2', 'factory': f2})
"""
class GracefulShutdownBusy(RunMixin, unittest.TestCase):
    def testShutdown(self):
        self.rmtree("basedir")
        os.mkdir("basedir")
        d = self.master.loadConfig(config_graceful_shutdown_busy)
        d.addCallback(lambda res: self.master.startService())
        d.addCallback(lambda res: self.connectSlave())

        def _send(res):
            # send a change. This will trigger both builders at the same
            # time, but since they share a slave, the max_builds=1 setting
            # will insure that only one of the two builds gets to run.
            cm = self.master.change_svc
            c = changes.Change("bob", ["Makefile", "foo/bar.c"],
                               "changed stuff")
            cm.addChange(c)
        d.addCallback(_send)

        def _delay(res):
            d1 = defer.Deferred()
            reactor.callLater(0.5, d1.callback, None)
            # this test depends upon this 0.5s delay landing us in the middle
            # of one of the builds.
            return d1
        d.addCallback(_delay)

        # Start a graceful shutdown.  We should be in the middle of two builds
        def _shutdown(res):
            bs = self.master.botmaster.builders['dummy'].slaves[0]
            # Monkeypatch the slave's shutdown routine since the real shutdown
            # interrupts the test harness
            self.did_shutdown = False
            def _shutdown():
                self.did_shutdown = True
                return defer.succeed(None)
            bs.slave.shutdown = _shutdown
            # Start a graceful shutdown
            bs.slave.slave_status.setGraceful(True)

            builders = [ self.master.botmaster.builders[bn]
                         for bn in ('dummy', 'dummy2') ]
            for builder in builders:
                self.failUnless(len(builder.slaves) == 1)
            building_bs = [ builder
                            for builder in builders
                            if builder.slaves[0].state == BUILDING ]
            # assert that both builds are running right now.
            self.failUnlessEqual(len(building_bs), 2)

        d.addCallback(_shutdown)

        # Wait a little bit again, and then make sure that we are still running
        # the two builds, and haven't shutdown yet
        d.addCallback(_delay)
        def _check(res):
            self.assertEquals(self.did_shutdown, False)
            builders = [ self.master.botmaster.builders[bn]
                         for bn in ('dummy', 'dummy2') ]
            for builder in builders:
                self.failUnless(len(builder.slaves) == 1)
            building_bs = [ builder
                            for builder in builders
                            if builder.slaves[0].state == BUILDING ]
            # assert that both builds are running right now.
            self.failUnlessEqual(len(building_bs), 2)
        d.addCallback(_check)

        # Wait for all the builds to finish
        def _wait_finish(res):
            builders = [ self.master.botmaster.builders[bn]
                         for bn in ('dummy', 'dummy2') ]
            builds = []
            for builder in builders:
                builds.append(builder.builder_status.currentBuilds[0].waitUntilFinished())
            dl = defer.DeferredList(builds)
            return dl
        d.addCallback(_wait_finish)

        # Wait a little bit after the builds finish, and then
        # check that the slave has shutdown
        d.addCallback(_delay)
        def _check_shutdown(res):
            # assert that we shutdown the slave
            self.assertEquals(self.did_shutdown, True)
            builders = [ self.master.botmaster.builders[bn]
                         for bn in ('dummy', 'dummy2') ]
            building_bs = [ builder
                            for builder in builders
                            if builder.slaves[0].state == BUILDING ]
            # assert that no builds are running right now.
            self.failUnlessEqual(len(building_bs), 0)
        d.addCallback(_check_shutdown)

        return d
