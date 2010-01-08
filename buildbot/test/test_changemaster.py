# -*- test-case-name: buildbot.test.test_changemaster -*-

import os
from twisted.trial import unittest

from twisted.application import service
from buildbot import db
from buildbot.changes.changes import Change
from buildbot.changes.manager import ChangeManager

class _DummyParent(service.MultiService):
    def __init__(self, basedir):
        service.MultiService.__init__(self)
        self.flags = []
        if not os.path.isdir(basedir):
            os.makedirs(basedir)
        spec = db.DB("sqlite3", os.path.join(basedir, "state.sqlite"))
        db.create_db(spec)
        self.db = db.open_db(spec)
    def changesWereAdded(self):
        self.flags.append("added")

class TestManager(unittest.TestCase):
    def testAddChange(self):
        basedir = "changemaster/manager/addchange"
        parent = _DummyParent(basedir)
        m = ChangeManager()
        m.setServiceParent(parent)

        change = Change('user', [], 'comments', revision="123")
        m.addChange(change)

        self.failUnlessEqual(parent.flags, ["added"])
        events = list(m.eventGenerator())
        self.failUnlessEqual(len(events), 1)
        self.failUnlessEqual(events[0].who, "user")
        self.failUnlessEqual(events[0].files, [])
        self.failUnlessEqual(events[0].number, 1)
        c1 = m.getChangeNumberedNow(1)
        self.failUnlessIdentical(c1, events[0]) # should be cached
        r = m.getChangesGreaterThan(0)
        self.failUnlessEqual(r, [c1])

        d = m.getChangeByNumber(1)
        d.addCallback(lambda r: self.failUnlessEqual(r, c1))
        d.addCallback(lambda ign: m.getChangesByNumber([1]))
        d.addCallback(lambda r: self.failUnlessEqual(r, [c1]))

        change2 = Change('otheruser', ["foo.c"], "comments2", revision="124")
        d.addCallback(lambda ign: m.addChange(change2))
        def _then(ign):
            events = list(m.eventGenerator())
            self.failUnlessEqual(len(events), 2)
            self.failUnlessEqual(events[0].who, "otheruser")
            self.failUnlessEqual(events[0].files, ["foo.c"])
            self.failUnlessEqual(events[0].number, 2)
            self.failUnlessEqual(events[1], c1)
            c1a = m.getChangeNumberedNow(1)
            self.failUnlessIdentical(c1a, c1)
            c2 = m.getChangeNumberedNow(2)
            self.failUnlessIdentical(c2, events[0])
            r = m.getChangesGreaterThan(0)
            self.failUnlessEqual(r, [c1, c2])

            d = m.getChangeByNumber(2)
            d.addCallback(lambda r: self.failUnlessEqual(r, c2))
            d.addCallback(lambda ign: m.getChangesByNumber([2,1]))
            d.addCallback(lambda r: self.failUnlessEqual(r, [c2,c1]))
            return d
        d.addCallback(_then)
        return d
