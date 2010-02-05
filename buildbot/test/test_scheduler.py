# -*- test-case-name: buildbot.test.test_scheduler -*-

import os, time

from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.application import service
from twisted.spread import pb

from buildbot import scheduler, sourcestamp, status
from buildbot.changes.changes import Change
from buildbot.scripts import tryclient

from buildbot.test.runutils import MasterMixin


class FakeMaster(service.MultiService):
    d = None
    def submitBuildSet(self, bs):
        self.sets.append(bs)
        if self.d:
            reactor.callLater(0, self.d.callback, bs)
            self.d = None
        return pb.Referenceable() # makes the cleanup work correctly

class Scheduling(MasterMixin, unittest.TestCase):
    def addScheduler(self, s):
        return self.master.scheduler_manager.updateSchedulers([s])

    def testPeriodic1(self):
        self.basedir = "scheduler/Scheduling/Periodic1"
        self.create_master()

        def _delay(res):
            d1 = defer.Deferred()
            reactor.callLater(2, d1.callback, None)
            return d1

        t = [0]
        def getCurrentTime():
            print "TIME IS", t[0]
            return t[0]

        def addTime(n):
            t[0] += n
            print "NEW TIME IS", t[0]

        s = scheduler.Periodic("quickly", ["a", "b"], 2)
        s.getCurrentTime = getCurrentTime

        d = self.addScheduler(s)
        d.addCallback(lambda ign: self.master.scheduler_manager.trigger())
        d2 = self.master.scheduler_manager.when_quiet()
        d.addCallback(lambda ign: d2)

        d.addCallback(lambda ign: addTime(3))
        d.addCallback(lambda ign: self.master.scheduler_manager.trigger())
        d3 = self.master.scheduler_manager.when_quiet()
        d.addCallback(lambda ign: d3)
        d.addCallback(self._testPeriodic1_1)
        return d
    def _testPeriodic1_1(self, res):
        active_sets = self.master.db.get_active_buildset_ids()
        self.failUnless(len(active_sets) > 1, active_sets)

        requests = self.master.db.get_buildrequestids_for_buildset(active_sets[0])
        self.failUnlessEqual(len(requests), 2, requests)
        builderNames = sorted(requests.keys())
        self.failUnlessEqual(builderNames, ["a","b"])

        info = self.master.db.get_buildset_info(active_sets[0])
        self.failUnlessEqual(info[1], "The Periodic scheduler named 'quickly' triggered this build")

    def testNightly(self):
        # now == 15-Nov-2005, 00:05:36 AM . By using mktime, this is
        # converted into the local timezone, which happens to match what
        # Nightly is going to do anyway.
        MIN=60; HOUR=60*MIN; DAY=24*3600
        now = time.mktime((2005, 11, 15, 0, 5, 36, 1, 319, -1))

        s = scheduler.Nightly('nightly', ["a"], hour=3)
        t = s.calculateNextRunTimeFrom(now)
        self.failUnlessEqual(int(t-now), 2*HOUR+54*MIN+24)

        s = scheduler.Nightly('nightly', ["a"], minute=[3,8,54])
        t = s.calculateNextRunTimeFrom(now)
        self.failUnlessEqual(int(t-now), 2*MIN+24)

        s = scheduler.Nightly('nightly', ["a"],
                              dayOfMonth=16, hour=1, minute=6)
        t = s.calculateNextRunTimeFrom(now)
        self.failUnlessEqual(int(t-now), DAY+HOUR+24)

        s = scheduler.Nightly('nightly', ["a"],
                              dayOfMonth=16, hour=1, minute=3)
        t = s.calculateNextRunTimeFrom(now)
        self.failUnlessEqual(int(t-now), DAY+57*MIN+24)

        s = scheduler.Nightly('nightly', ["a"],
                              dayOfMonth=15, hour=1, minute=3)
        t = s.calculateNextRunTimeFrom(now)
        self.failUnlessEqual(int(t-now), 57*MIN+24)

        s = scheduler.Nightly('nightly', ["a"],
                              dayOfMonth=15, hour=0, minute=3)
        t = s.calculateNextRunTimeFrom(now)
        self.failUnlessEqual(int(t-now), 30*DAY-3*MIN+24)


    def isImportant(self, change):
        if "important" in change.files:
            return True
        return False

    def testBranch(self):
        s = scheduler.Scheduler("b1", "branch1", 2, ["a","b"],
                                fileIsImportant=self.isImportant)
        self.addScheduler(s)

        c0 = Change("carol", ["important"], "other branch", branch="other")
        s.addChange(c0)
        self.failIf(s.timer)
        self.failIf(s.importantChanges)

        c1 = Change("alice", ["important", "not important"], "some changes",
                    branch="branch1")
        s.addChange(c1)
        c2 = Change("bob", ["not important", "boring"], "some more changes",
                    branch="branch1")
        s.addChange(c2)
        c3 = Change("carol", ["important", "dull"], "even more changes",
                    branch="branch1")
        s.addChange(c3)
        
        self.failUnlessEqual(s.importantChanges, [c1,c3])
        self.failUnlessEqual(s.allChanges, [c1,c2,c3])
        self.failUnless(s.timer)

        d = defer.Deferred()
        reactor.callLater(4, d.callback, None)
        d.addCallback(self._testBranch_1)
        return d
    def _testBranch_1(self, res):
        self.failUnlessEqual(len(self.master.sets), 1)
        s = self.master.sets[0].source
        self.failUnlessEqual(s.branch, "branch1")
        self.failUnlessEqual(s.revision, None)
        self.failUnlessEqual(len(s.changes), 3)
        self.failUnlessEqual(s.patch, None)


    def testAnyBranch(self):
        s = scheduler.AnyBranchScheduler("b1", None, 1, ["a","b"],
                                         fileIsImportant=self.isImportant)
        self.addScheduler(s)

        c1 = Change("alice", ["important", "not important"], "some changes",
                    branch="branch1")
        s.addChange(c1)
        c2 = Change("bob", ["not important", "boring"], "some more changes",
                    branch="branch1")
        s.addChange(c2)
        c3 = Change("carol", ["important", "dull"], "even more changes",
                    branch="branch1")
        s.addChange(c3)

        c4 = Change("carol", ["important"], "other branch", branch="branch2")
        s.addChange(c4)

        c5 = Change("carol", ["important"], "default branch", branch=None)
        s.addChange(c5)

        d = defer.Deferred()
        reactor.callLater(2, d.callback, None)
        d.addCallback(self._testAnyBranch_1)
        return d
    def _testAnyBranch_1(self, res):
        self.failUnlessEqual(len(self.master.sets), 3)
        self.master.sets.sort(lambda a,b: cmp(a.source.branch,
                                              b.source.branch))

        s1 = self.master.sets[0].source
        self.failUnlessEqual(s1.branch, None)
        self.failUnlessEqual(s1.revision, None)
        self.failUnlessEqual(len(s1.changes), 1)
        self.failUnlessEqual(s1.patch, None)

        s2 = self.master.sets[1].source
        self.failUnlessEqual(s2.branch, "branch1")
        self.failUnlessEqual(s2.revision, None)
        self.failUnlessEqual(len(s2.changes), 3)
        self.failUnlessEqual(s2.patch, None)

        s3 = self.master.sets[2].source
        self.failUnlessEqual(s3.branch, "branch2")
        self.failUnlessEqual(s3.revision, None)
        self.failUnlessEqual(len(s3.changes), 1)
        self.failUnlessEqual(s3.patch, None)

    def testAnyBranch2(self):
        # like testAnyBranch but without fileIsImportant
        s = scheduler.AnyBranchScheduler("b1", None, 2, ["a","b"])
        self.addScheduler(s)
        c1 = Change("alice", ["important", "not important"], "some changes",
                    branch="branch1")
        s.addChange(c1)
        c2 = Change("bob", ["not important", "boring"], "some more changes",
                    branch="branch1")
        s.addChange(c2)
        c3 = Change("carol", ["important", "dull"], "even more changes",
                    branch="branch1")
        s.addChange(c3)

        c4 = Change("carol", ["important"], "other branch", branch="branch2")
        s.addChange(c4)

        d = defer.Deferred()
        reactor.callLater(2, d.callback, None)
        d.addCallback(self._testAnyBranch2_1)
        return d
    def _testAnyBranch2_1(self, res):
        self.failUnlessEqual(len(self.master.sets), 2)
        self.master.sets.sort(lambda a,b: cmp(a.source.branch,
                                              b.source.branch))
        s1 = self.master.sets[0].source
        self.failUnlessEqual(s1.branch, "branch1")
        self.failUnlessEqual(s1.revision, None)
        self.failUnlessEqual(len(s1.changes), 3)
        self.failUnlessEqual(s1.patch, None)

        s2 = self.master.sets[1].source
        self.failUnlessEqual(s2.branch, "branch2")
        self.failUnlessEqual(s2.revision, None)
        self.failUnlessEqual(len(s2.changes), 1)
        self.failUnlessEqual(s2.patch, None)


    def createMaildir(self, jobdir):
        os.mkdir(jobdir)
        os.mkdir(os.path.join(jobdir, "new"))
        os.mkdir(os.path.join(jobdir, "cur"))
        os.mkdir(os.path.join(jobdir, "tmp"))

    jobcounter = 1
    def pushJob(self, jobdir, job):
        while 1:
            filename = "job_%d" % self.jobcounter
            self.jobcounter += 1
            if os.path.exists(os.path.join(jobdir, "new", filename)):
                continue
            if os.path.exists(os.path.join(jobdir, "tmp", filename)):
                continue
            if os.path.exists(os.path.join(jobdir, "cur", filename)):
                continue
            break
        f = open(os.path.join(jobdir, "tmp", filename), "w")
        f.write(job)
        f.close()
        os.rename(os.path.join(jobdir, "tmp", filename),
                  os.path.join(jobdir, "new", filename))

    def testTryJobdir(self):
        self.master.basedir = "try_jobdir"
        os.mkdir(self.master.basedir)
        jobdir = "jobdir1"
        jobdir_abs = os.path.join(self.master.basedir, jobdir)
        self.createMaildir(jobdir_abs)
        s = scheduler.Try_Jobdir("try1", ["a", "b"], jobdir)
        self.addScheduler(s)
        self.failIf(self.master.sets)
        job1 = tryclient.createJobfile("buildsetID",
                                       "branch1", "123", 1, "diff",
                                       ["a", "b"])
        self.master.d = d = defer.Deferred()
        self.pushJob(jobdir_abs, job1)
        d.addCallback(self._testTryJobdir_1)
        # N.B.: if we don't have DNotify, we poll every 10 seconds, so don't
        # set a .timeout here shorter than that. TODO: make it possible to
        # set the polling interval, so we can make it shorter.
        return d

    def _testTryJobdir_1(self, bs):
        self.failUnlessEqual(bs.builderNames, ["a", "b"])
        self.failUnlessEqual(bs.source.branch, "branch1")
        self.failUnlessEqual(bs.source.revision, "123")
        self.failUnlessEqual(bs.source.patch, (1, "diff"))


    def testTryUserpass(self):
        up = [("alice","pw1"), ("bob","pw2")]
        s = scheduler.Try_Userpass("try2", ["a", "b"], 0, userpass=up)
        self.addScheduler(s)
        port = s.getPort()
        config = {'connect': 'pb',
                  'username': 'alice',
                  'passwd': 'pw1',
                  'master': "localhost:%d" % port,
                  'builders': ["a", "b"],
                  }
        t = tryclient.Try(config)
        ss = sourcestamp.SourceStamp("branch1", "123", (1, "diff"))
        t.sourcestamp = ss
        d2 = self.master.d = defer.Deferred()
        d = t.deliverJob()
        d.addCallback(self._testTryUserpass_1, t, d2)
        return d
    testTryUserpass.timeout = 5
    def _testTryUserpass_1(self, res, t, d2):
        # at this point, the Try object should have a RemoteReference to the
        # status object. The FakeMaster returns a stub.
        self.failUnless(t.buildsetStatus)
        d2.addCallback(self._testTryUserpass_2, t)
        return d2
    def _testTryUserpass_2(self, bs, t):
        # this should be the BuildSet submitted by the TryScheduler
        self.failUnlessEqual(bs.builderNames, ["a", "b"])
        self.failUnlessEqual(bs.source.branch, "branch1")
        self.failUnlessEqual(bs.source.revision, "123")
        self.failUnlessEqual(bs.source.patch, (1, "diff"))

        t.cleanup()

        # twisted-2.0.1 (but not later versions) seems to require a reactor
        # iteration before stopListening actually works. TODO: investigate
        # this.
        d = defer.Deferred()
        reactor.callLater(0, d.callback, None)
        return d

    def testGetBuildSets(self):
        # validate IStatus.getBuildSets
        s = status.builder.Status(None, ".")
        bs1 = BuildSet(["a","b"], sourcestamp.SourceStamp(),
                                reason="one", bsid="1")
        s.buildsetSubmitted(bs1.status)
        self.failUnlessEqual(s.getBuildSets(), [bs1.status])
        bs1.status.notifyFinishedWatchers()
        self.failUnlessEqual(s.getBuildSets(), [])

    def testCategory(self):
        s1 = scheduler.Scheduler("b1", "branch1", 2, ["a","b"], categories=["categoryA", "both"])
        self.addScheduler(s1)
        s2 = scheduler.Scheduler("b2", "branch1", 2, ["a","b"], categories=["categoryB", "both"])
        self.addScheduler(s2)

        c0 = Change("carol", ["important"], "branch1", branch="branch1", category="categoryA")
        s1.addChange(c0)
        s2.addChange(c0)

        c1 = Change("carol", ["important"], "branch1", branch="branch1", category="categoryB")
        s1.addChange(c1)
        s2.addChange(c1)

        c2 = Change("carol", ["important"], "branch1", branch="branch1")
        s1.addChange(c2)
        s2.addChange(c2)

        c3 = Change("carol", ["important"], "branch1", branch="branch1", category="both")
        s1.addChange(c3)
        s2.addChange(c3)

        self.failUnlessEqual(s1.importantChanges, [c0, c3])
        self.failUnlessEqual(s2.importantChanges, [c1, c3])

        s = scheduler.Scheduler("b3", "branch1", 2, ["a","b"])
        self.addScheduler(s)

        c0 = Change("carol", ["important"], "branch1", branch="branch1", category="categoryA")
        s.addChange(c0)
        c1 = Change("carol", ["important"], "branch1", branch="branch1", category="categoryB")
        s.addChange(c1)

        self.failUnlessEqual(s.importantChanges, [c0, c1])
