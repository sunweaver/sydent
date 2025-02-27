# -*- coding: utf-8 -*-

# Copyright 2016 OpenMarket Ltd
# Copyright 2017 Vector Creations Ltd
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
from __future__ import absolute_import

import logging
import phonenumbers

from sydent.db.valsession import ThreePidValSessionStore
from sydent.validators import common
from sydent.sms.openmarket import OpenMarketSMS

from sydent.validators import DestinationRejectedException

from sydent.util import time_msec

logger = logging.getLogger(__name__)


class MsisdnValidator:
    def __init__(self, sydent):
        self.sydent = sydent
        self.omSms = OpenMarketSMS(sydent)

        # cache originators & sms rules from config file
        self.originators = {}
        self.smsRules = {}
        for opt in self.sydent.cfg.options("sms"):
            if opt.startswith("originators."):
                country = opt.split(".")[1]
                rawVal = self.sydent.cfg.get("sms", opt)
                rawList = [i.strip() for i in rawVal.split(",")]

                self.originators[country] = []
                for origString in rawList:
                    parts = origString.split(":")
                    if len(parts) != 2:
                        raise Exception(
                            "Originators must be in form: long:<number>, short:<number> or alpha:<text>, separated by commas"
                        )
                    if parts[0] not in ["long", "short", "alpha"]:
                        raise Exception(
                            "Invalid originator type: valid types are long, short and alpha"
                        )
                    self.originators[country].append(
                        {
                            "type": parts[0],
                            "text": parts[1],
                        }
                    )
            elif opt.startswith("smsrule."):
                country = opt.split(".")[1]
                action = self.sydent.cfg.get("sms", opt)

                if action not in ["allow", "reject"]:
                    raise Exception(
                        "Invalid SMS rule action: %s, expecting 'allow' or 'reject'"
                        % action
                    )

                self.smsRules[country] = action

    def requestToken(self, phoneNumber, clientSecret, sendAttempt, brand=None):
        """
        Creates or retrieves a validation session and sends an text message to the
        corresponding phone number address with a token to use to verify the association.

        :param phoneNumber: The phone number to send the email to.
        :type phoneNumber: phonenumbers.PhoneNumber
        :param clientSecret: The client secret to use.
        :type clientSecret: unicode
        :param sendAttempt: The current send attempt.
        :type sendAttempt: int
        :param brand: A hint at a brand from the request.
        :type brand: str or None

        :return: The ID of the session created (or of the existing one if any)
        :rtype: int
        """
        if str(phoneNumber.country_code) in self.smsRules:
            action = self.smsRules[str(phoneNumber.country_code)]
            if action == "reject":
                raise DestinationRejectedException()

        valSessionStore = ThreePidValSessionStore(self.sydent)

        msisdn = phonenumbers.format_number(
            phoneNumber, phonenumbers.PhoneNumberFormat.E164
        )[1:]

        valSession = valSessionStore.getOrCreateTokenSession(
            medium="msisdn", address=msisdn, clientSecret=clientSecret
        )

        valSessionStore.setMtime(valSession.id, time_msec())

        if int(valSession.sendAttemptNumber) >= int(sendAttempt):
            logger.info(
                "Not texting code because current send attempt (%d) is not less than given send attempt (%s)",
                int(sendAttempt),
                int(valSession.sendAttemptNumber),
            )
            return valSession.id

        smsBodyTemplate = self.sydent.cfg.get("sms", "bodyTemplate")
        originator = self.getOriginator(phoneNumber)

        logger.info(
            "Attempting to text code %s to %s (country %d) with originator %s",
            valSession.token,
            msisdn,
            phoneNumber.country_code,
            originator,
        )

        smsBody = smsBodyTemplate.format(token=valSession.token)

        self.omSms.sendTextSMS(smsBody, msisdn, originator)

        valSessionStore.setSendAttemptNumber(valSession.id, sendAttempt)

        return valSession.id

    def getOriginator(self, destPhoneNumber):
        """
        Gets an originator for a given phone number.

        :param destPhoneNumber: The phone number to find the originator for.
        :type destPhoneNumber: phonenumbers.PhoneNumber

        :return: The originator (a dict with a "type" key and a "text" key).
        :rtype: dict[str, str]
        """
        countryCode = str(destPhoneNumber.country_code)

        origs = [
            {
                "type": "alpha",
                "text": "Matrix",
            }
        ]
        if countryCode in self.originators:
            origs = self.originators[countryCode]
        elif "default" in self.originators:
            origs = self.originators["default"]

        # deterministically pick an originator from the list of possible
        # originators, so if someone requests multiple codes, they come from
        # a consistent number (if there's any chance that some originators are
        # more likley to work than others, we may want to change, but it feels
        # like this should be something other than just picking one randomly).
        msisdn = phonenumbers.format_number(
            destPhoneNumber, phonenumbers.PhoneNumberFormat.E164
        )[1:]
        return origs[sum([int(i) for i in msisdn]) % len(origs)]

    def validateSessionWithToken(self, sid, clientSecret, token):
        """
        Validates the session with the given ID.

        :param sid: The ID of the session to validate.
        :type sid: unicode
        :param clientSecret: The client secret to validate.
        :type clientSecret: unicode
        :param token: The token to validate.
        :type token: unicode

        :return: A dict with a "success" key which is True if the session
            was successfully validated, False otherwise.
        :rtype: dict[str, bool]
        """
        return common.validateSessionWithToken(self.sydent, sid, clientSecret, token)
