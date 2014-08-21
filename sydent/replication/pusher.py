# -*- coding: utf-8 -*-

# Copyright 2014 matrix.org
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

import twisted.internet.reactor
import twisted.internet.task

from sydent.replication.peer import LocalPeer
from sydent.db.threepid_associations import LocalAssociationStore
from sydent.db.peers import PeerStore
from sydent.threepid.assocsigner import AssociationSigner

logger = logging.getLogger(__name__)


class Pusher:
    def __init__(self, sydent):
        self.sydent = sydent
        self.pushing = False
        self.peerStore = PeerStore(self.sydent)

    def setup(self):
        cb = twisted.internet.task.LoopingCall(Pusher.scheduledPush, self)
        cb.start(10.0)

    def getSignedAssociationsAfterId(self, afterId, limit):
        signedAssocs = {}

        localAssocStore = LocalAssociationStore(self.sydent)
        localAssocs = localAssocStore.getAssociationsAfterId(afterId, limit)

        assocSigner = AssociationSigner(self.sydent)

        for localId in localAssocs:
            sgAssoc = assocSigner.signedThreePidAssociation(localAssocs[localId])
            signedAssocs[localId] = sgAssoc

        return signedAssocs

    def doLocalPush(self):
        """
        Synchronously push local associations to this server (ie. copy them to globals table)
        The local server is essentially treated the same as any other peer except we don't do the
        network round-trip and this function can be used so the association goes into the global table
        before the http call returns (so clients know it will be available on at least the same ID server they used)
        """
        localPeer = LocalPeer(self.sydent)

        signedAssocs = self.getSignedAssociationsAfterId(localPeer.lastId, None)

        localPeer.pushUpdates(signedAssocs)

    def scheduledPush(self):
        if self.pushing:
            return
        self.pushing = True

        updateDeferred = None

        try:
            peers = self.peerStore.getAllPeers()

            for p in peers:
                logger.debug("Looking for update after %d to push to %s", p.lastSentVersion, p.servername)
                signedAssocTuples = self.getSignedAssociationsAfterId(p.lastSentVersion, 100)
                logger.debug("%d updates to push to %s", len(signedAssocTuples), p.servername)
                if len(signedAssocTuples) > 0:
                    logger.info("Pushing %d updates to %s", len(signedAssocTuples), p.servername)
                    updateDeferred = p.pushUpdates(signedAssocTuples)
                    updateDeferred.addCallback(self._pushSucceeded, peer=p)
                    updateDeferred.addErrback(self._pushFailed, peer=p)
                    break
        finally:
            if not updateDeferred:
                self.pushing = False

    def _pushSucceeded(self, result, peer):
        logger.info("Pushed updates to %s with result %d %s", peer.servername, result.code, result.phrase)
        self.pushing = False
        self.scheduledPush()

    def _pushFailed(self, failure, peer):
        logger.info("Failed to push updates to %s: %s", peer.servername, failure)
        self.pushing = False
        return None
