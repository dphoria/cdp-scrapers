#!/usr/bin/env python
# -*- coding: utf-8 -*-

from typing import Dict, List
from bs4 import BeautifulSoup
from urllib.request import urlopen
from urllib.error import HTTPError
import re
import sys
import datetime

from cdp_backend.pipeline.ingestion_models import (
    EventIngestionModel,
    Body,
    Session,
    EventMinutesItem,
    MinutesItem,
    Matter,
    SupportingFile,
)
from cdp_scrapers.legistar_utils import(
    get_legistar_events_for_timespan,
    LEGISTAR_BASE
)

###############################################################################


# e.g. MinutesItem.name     =  EventItemEventId from legistar api
LEGISTAR_MINUTE_NAME        = 'EventItemEventId'
LEGISTAR_EV_MINUTE_DECISION = 'EventItemPassedFlagName'
LEGISTAR_MATTER_EXT_ID      = 'EventItemMatterId'
LEGISTAR_MATTER_TITLE       = 'EventItemTitle'
LEGISTAR_MATTER_NAME        = 'EventItemMatterName'
LEGISTAR_MATTER_TYPE        = 'EventItemMatterType'
LEGISTAR_MATTER_STATUS      = 'EventItemMatterStatus'
# Session.session_datetime is a combo of EventDate and EventTime
LEGISTAR_SESSION_DATE       = 'EventDate'
LEGISTAR_SESSION_TIME       = 'EventTime'
LEGISTAR_BODY_NAME          = 'EventBodyName'
LEGISTAR_FILE_EXT_ID        = 'MatterAttachmentId'
LEGISTAR_FILE_NAME          = 'MatterAttachmentName'
LEGISTAR_FILE_URI           = 'MatterAttachmentHyperlink'

LEGISTAR_EV_ITEMS           = 'EventItems'
LEGISTAR_EV_SITE_URL        = 'EventInSiteURL'
LEGISTAR_EV_ATTACHMENTS     = 'EventItemMatterAttachments'

CDP_VIDEO_URI   = 'video_uri'
CDP_CAPTION_URI = 'caption_uri'


# base class for scraper to convert legistar api data -> cdp ingestion model data
# TODO: double-check unique class name in cdp to avoid confusion
class LegistarScraper:
    def __init__(self, client: str):
        self.client_name = client


    def is_legistar_compatible(self) -> bool:
        '''
        return True if can get successful legistar api response
        '''
        client = self.client_name

        # simplest check, if the GET request works, it is a legistar place
        try:
            resp = urlopen(f'{LEGISTAR_BASE}/bodies')
            return (resp.status == 200)
        except:
            return False


    # TODO: better name?
    def can_get_min_info(self, check_days: int = 7) -> bool:
        '''
        return False if never can get minimum required data for EventIngestionModel within check_days from today
        '''
        # if not self.is_legistar_compatible():
        #     return False

        now = datetime.datetime.utcnow()
        days = range(check_days)

        for d in days:
            # ev: EventIngestionModel
            for cdp_ev in self.get_events(
                begin_t = now - datetime.timedelta(days = d + 1),
                end_t   = now - datetime.timedelta(days = d)
            ):
                try:
                    if len(cdp_ev.body.name) > 0 and \
                       cdp_ev.sessions[0].session_datetime is not None and \
                       len(cdp_ev.sessions[0].video_uri) > 0:
                        return True
                except:
                    pass

        # no event in check_days had enough for minimal ingestion model item
        return False


    def get_event_support_files(self, legistar_ev_attachments: List[Dict]) -> List[SupportingFile]:
        '''
        return SupportingFiles from legistar EventItemMatterAttachments
        '''
        files = []

        for attachment in legistar_ev_attachments:
            files.append(
                SupportingFile(
                    external_source_id = attachment[LEGISTAR_FILE_EXT_ID],
                    name = attachment[LEGISTAR_FILE_NAME],
                    uri = attachment[LEGISTAR_FILE_URI],
                )
            )

        return files


    def get_event_minutes(self, legistar_ev_items: List[Dict]) -> List[EventMinutesItem]:
        '''
        return legistar 'EventItems' as EventMinutesItems
        '''
        # TODO: create more objects like Vote, SupportingFile, Person with available info

        minutes = []

        # EventMinutesItem object per member in EventItems
        for item in legistar_ev_items:
            minutes.append(
                EventMinutesItem(
                    decision = item[LEGISTAR_EV_MINUTE_DECISION],
                    # other better choice for name?
                    minutes_item = MinutesItem(name = item[LEGISTAR_MINUTE_NAME]),
                    supporting_files = self.get_event_support_files(item[LEGISTAR_EV_ATTACHMENTS]),

                    matter = Matter(
                        external_source_id = item[LEGISTAR_MATTER_EXT_ID],
                        name = item[LEGISTAR_MATTER_NAME],
                        matter_type = item[LEGISTAR_MATTER_TYPE],
                        title = item[LEGISTAR_MATTER_TITLE],
                        result_status = item[LEGISTAR_MATTER_STATUS],
                    )
                )
            )

        return minutes


    def get_video_uris(self, legistar_ev: Dict) -> List[Dict]:
        '''
        return url for videos and captions if found in data set from legistar api

        returned data is like
        [{'video_uri' : 'https://video.mp4', 'caption_uri' : 'https://caption.vtt'}, ...]
        '''
        return []


    @staticmethod
    def to_datetime(ev_date: str, ev_time: str) -> datetime.datetime:
        '''
        helper func combine legistar ev date and time into datetime
        '''
        # 2021-07-09T00:00:00
        d = datetime.datetime.strptime(ev_date, '%Y-%m-%dT%H:%M:%S')
        # 9:30 AM
        t = datetime.datetime.strptime(ev_time, '%I:%M %p')
        return datetime.datetime(
            year = d.year, month = d.month, day = d.day,
            hour = t.hour, minute = t.minute, second = t.second
        )


    def get_events(self,
        # for the past 2 days
        begin: datetime.time = datetime.datetime.utcnow() - datetime.timedelta(days = 2),
        end:   datetime.time = datetime.datetime.utcnow()
    ) -> List[EventIngestionModel]:
        '''
        main getter to retrieve legistar data as cdp ingestion model items
        '''
        evs = []

        for legistar_ev in get_legistar_events_for_timespan(
            self.client_name,
            begin = begin,
            end = end,
            # NOTE: use the range below to retrieve events with 1 containing video
            #begin = datetime.datetime(year = 2021, month = 7, day = 9),
            #end = datetime.datetime(year = 2021, month = 7, day = 10),
        ):
            session_time = self.to_datetime(legistar_ev[LEGISTAR_SESSION_DATE], legistar_ev[LEGISTAR_SESSION_TIME])
            sessions = []

            # TODO: Session per video_uri/caption_uri ok?
            for uri in self.get_video_uris(legistar_ev):
                sessions.append(
                    Session(
                        session_datetime = session_time,
                        session_index = len(sessions),
                        video_uri = uri[CDP_VIDEO_URI],
                        caption_uri = uri[CDP_CAPTION_URI],
                    )
                )
            else:
                # found 0 videos
                sessions.append(
                    Session(
                        session_datetime = session_time,
                        session_index = 0,
                        video_uri = None,
                    )
                )

            evs.append(
                EventIngestionModel(
                    body = Body(name = legistar_ev[LEGISTAR_BODY_NAME]),
                    event_minutes_items = self.get_event_minutes(legistar_ev[LEGISTAR_EV_ITEMS]),
                    sessions = sessions,
                )
            )

        return evs


class SeattleScraper(LegistarScraper):
    def __init__(self):
        super().__init__('seattle')


    def get_video_uris(self, legistar_ev: Dict) -> List[Dict]:
        # broad try to simply return empty list if any statement below fails
        try:
            # EventInSiteURL (= MeetingDetail.aspx) has a td tag with a certain id pattern containing url to video
            with urlopen(legistar_ev[LEGISTAR_EV_SITE_URL]) as resp:
                soup = BeautifulSoup(resp.read(), 'html.parser')
                # this gets us the url for the web PAGE containing the video
                video_page_url = soup.find('a', id = re.compile(r'ct\S*_ContentPlaceHolder\S*_hypVideo'), class_ = 'videolink')['href']

            with urlopen(video_page_url) as resp:
                # now load the page to get the actual video url
                soup = BeautifulSoup(resp.read(), 'html.parser')

            # <script>
            # ...
            # playerInstance.setup({
            # sources: [
            #     {
            #         file: "//video.seattle.gov/media/council/fin_070921_2612127V.mp4",
            #         label: "Auto"
            #     }
            # ],
            # ...
            # tracks: [{
            #     file: "documents/seattlechannel/closedcaption/2021/fin_070921_2612127.vtt",
            #     label: "English",
            #     kind: "captions",
            #     "default": true
            # }
            # 
            #  ], 
            # ...

            # entire script tag text that has the video player setup call
            video_script_text = soup.find('script', text = re.compile(r'playerInstance\.setup')).string
            # playerSetup({...
            #             ^
            player_arg_start = re.search(r'playerInstance\.setup\((\{)', video_script_text).start(1)
            # ...});
            #     ^
            # playerInstance... # more playerInstance code
            video_json_blob = video_script_text[
                player_arg_start :
                player_arg_start + re.search(r'\)\;\s*\n\s*playerInstance', video_script_text[player_arg_start:]).start(0)
            ]

            # not smart enough to make a cool one-time regex to get all the 'file's in 'sources'
            videos_start = video_json_blob.find('sources:')
            videos_end = video_json_blob.find('],', videos_start)
            # as shown above, url will start with // so prepend https:
            video_uris = [ \
                'https:' + i for i in re.findall(
                    r'file\:\s*\"([^\"]+)',
                    video_json_blob[videos_start : videos_end],
                )
            ]

            captions_start = video_json_blob.find('tracks:')
            captions_end = video_json_blob.find('],', captions_start)
            caption_uris = [ \
                'https://www.seattlechannel.org/' + i for i in re.findall(
                    r'file\:\s*\"([^\"]+)',
                    video_json_blob[captions_start : captions_end],
                )
            ]

        except:
            return []

        iter = range(len(video_uris))
        list_uri = []

        for i in iter:
            # TODO: ok to assume # video_uris == # caption_uris on a meeting details web page on seattlechannel.org ?
            list_uri.append({CDP_VIDEO_URI : video_uris[i], CDP_CAPTION_URI : caption_uris[i]})

        return list_uri
