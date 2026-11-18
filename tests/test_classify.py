from datetime import datetime, timedelta, timezone

import pytest

from focustracker.classify import Classifier
from focustracker.config import example_config_path, load_config
from focustracker.models import Event

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def classifier():
    return Classifier(load_config(example_config_path()), use_llm=False)


def event(app="Google Chrome", title="", url="", incognito=False) -> Event:
    return Event(T0, T0 + timedelta(seconds=60), app, title, url, incognito)


def thread_of(classifier, **kwargs) -> str:
    return classifier.classify([event(**kwargs)])[0].thread


def test_company_mention_wins_even_in_gmail(classifier):
    ev = classifier.classify(
        [event(title="Re: Interview with Northwind Labs - Gmail", url="https://mail.google.com/mail/u/0")]
    )[0]
    assert ev.thread == "Job search: Northwind Labs"
    assert ev.is_comms  # counts as task comms
    assert ev.label == "Gmail"


def test_company_alias(classifier):
    assert thread_of(classifier, url="https://careers.bluefin.io/jobs/42") == "Job search: Bluefin Analytics"


def test_company_captured_from_ats_url(classifier):
    url = "https://jobs.lever.co/acme-robotics/1234/apply"
    assert thread_of(classifier, title="Apply", url=url) == "Job search: Acme Robotics"


def test_ats_slug_maps_to_configured_company(classifier):
    url = "https://boards.greenhouse.io/northwindlabs/jobs/1"
    assert thread_of(classifier, title="Job", url=url) == "Job search: Northwind Labs"


def test_rule_order_linkedin_jobs_before_linkedin(classifier):
    assert thread_of(classifier, url="https://www.linkedin.com/jobs/view/1") == "Job search: job boards"
    assert thread_of(classifier, url="https://www.linkedin.com/feed/") == "Job search: LinkedIn"


def test_youtube_tutorial_is_learning_not_drift(classifier):
    url = "https://www.youtube.com/watch?v=x"
    assert thread_of(classifier, title="React tutorial for beginners", url=url) == "Learning: videos"
    assert thread_of(classifier, title="Funny cats", url=url) == "Drift: video"


def test_general_comms(classifier):
    ev = classifier.classify([event(app="Telegram", title="Telegram")])[0]
    assert (ev.thread, ev.kind, ev.is_comms) == ("Comms", "work", True)


def test_unmatched_is_unknown_in_rules_only_mode(classifier):
    ev = classifier.classify([event(app="SomeApp", title="Something")])[0]
    assert (ev.thread, ev.source, ev.kind) == ("Unknown", "unclassified", "neutral")


def test_incognito_is_private(classifier):
    ev = classifier.classify([event(title="secret", url="https://example.com", incognito=True)])[0]
    assert (ev.thread, ev.source) == ("Private browsing", "private")
