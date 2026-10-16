"""The help centre: topic cards, article cards, a readable article page, safe rich text, and staff controls over how it is drawn."""
import pytest
from django.urls import reverse

from apps.support import kb
from apps.support.models import KBArticle, KBCategory

pytestmark = pytest.mark.django_db


@pytest.fixture
def library():
    first = KBCategory.objects.create(name="Library one", slug="library-one", icon="bi-envelope", description="About the first shelf.", sort_order=1)
    second = KBCategory.objects.create(name="Library two", slug="library-two", icon="bi-globe2", sort_order=2)
    KBCategory.objects.create(name="Library empty", slug="library-empty", sort_order=3)
    a = KBArticle.objects.create(category=first, title="Set up the thing", slug="set-up-the-thing", summary="The quick way.", is_published=True, is_featured=True, sort_order=1,
                                 body="## Before you start\\n\\nYou need a **mailbox** and `code`.\\n\\n## The steps\\n\\n1. Open the page\\n2. Press the button\\n\\n- one\\n- two\\n\\n> Tip: keep it safe.\\n\\nSee [the other guide](/help/library-one/second-thing/).".replace("\\n", "\n"))
    b = KBArticle.objects.create(category=first, title="Second thing", slug="second-thing", summary="Next.", is_published=True, sort_order=2, body="Short text.")
    KBArticle.objects.create(category=first, title="Draft thing", slug="draft-thing", is_published=False, body="Not ready.")
    KBArticle.objects.create(category=second, title="Other shelf", slug="other-shelf", is_published=True, body="Hello.")
    return first, second, a, b


def test_rich_text_is_escaped_first_and_only_a_small_set_of_markup_comes_back():
    html, contents = kb.render_body("## Title\n\n<script>alert(1)</script> **bold** `x < y` [good](/help/) [bad](javascript:alert(1)) [web](https://example.org/a)\n\n- a\n- b\n\n1. one\n2. two\n\n> note")
    assert "<script" not in html and "&lt;script&gt;" in html and "<strong>bold</strong>" in html and "<code>x &lt; y</code>" in html
    assert '<a href="/help/">good</a>' in html and "javascript:" not in html.split("[bad]")[0].replace("[bad](javascript:alert(1))", "") and 'href="javascript' not in html
    assert 'rel="noopener noreferrer"' in html and '<ul class="help-checks"><li>a</li><li>b</li></ul>' in html and '<ol class="help-steps"><li>one</li><li>two</li></ol>' in html and 'class="help-note"' in html
    assert contents == [("title", "Title")] and '<h2 id="title">Title</h2>' in html


def test_the_front_page_shows_topic_cards_start_here_cards_and_a_way_to_ask(client, library):
    page = client.get(reverse("help:index"))
    html = page.content.decode()
    assert page.status_code == 200 and "How can we help?" in html and "help-search" in html
    assert 'class="help-topic"' in html and "Library one" in html and "About the first shelf." in html and "bi-envelope" in html and "2 articles" in html
    assert "Library empty" not in html  # a topic without a published article is not shown
    assert "Start here" in html and "Set up the thing" in html and "The quick way." in html  # the featured article is a card
    assert "Still need help?" in html and "Draft thing" not in html


def test_a_topic_lists_its_articles_as_cards_with_reading_time_and_the_other_topics_beside_them(client, library):
    html = client.get(reverse("help:category", args=["library-one"])).content.decode()
    assert "Set up the thing" in html and "The quick way." in html and "min read" in html and "Draft thing" not in html
    assert "Other topics" in html and "Library two" in html and 'aria-label="Breadcrumb"' in html
    assert client.get(reverse("help:category", args=["library-empty"])).status_code == 404


def test_an_article_reads_well_with_contents_related_articles_and_the_next_one(client, library):
    first, second, a, b = library
    html = client.get(reverse("help:article", args=["library-one", "set-up-the-thing"])).content.decode()
    assert '<h2 id="before-you-start">Before you start</h2>' in html and 'href="#the-steps"' in html and "In this article" in html
    assert "<strong>mailbox</strong>" in html and '<ol class="help-steps"><li>Open the page</li>' in html and "help-note" in html and "min read" in html
    assert "You may also need" in html and "Second thing" in html and "Next" in html and "Was this article helpful?" in html and "In this article" in html and "More in Library one" in html
    assert client.get(reverse("help:article", args=["library-one", "draft-thing"])).status_code == 404
    a.refresh_from_db()
    assert a.views == 1
    client.get(reverse("help:article", args=["library-one", "set-up-the-thing"]))
    a.refresh_from_db()
    assert a.views == 2
    assert [x.title for x in kb.popular()][0] == "Set up the thing"
    before, after = kb.neighbours(a)
    assert before is None and after.pk == b.pk


def test_search_finds_articles_and_says_when_it_finds_nothing(client, library):
    found = client.get(reverse("help:index") + "?q=mailbox").content.decode()
    assert "1 result" in found and "Set up the thing" in found and "Library one" in found
    nothing = client.get(reverse("help:index") + "?q=zzqxy").content.decode()
    assert "Nothing found" in nothing


def test_staff_set_the_icon_summary_and_featured_flag_and_a_bad_icon_name_is_cleaned(world, library):
    staff = world.people["super admin"]
    category = kb.save_category(staff, None, name="Fresh shelf", description="New", icon="bi-gear<script>", sort_order=5)
    assert category.icon == "bi-gearscript"  # only letters, digits and dashes survive
    article = kb.save_article(staff, None, category=category, title="Fresh guide", body="## Hi\n\nText", summary="Short text", is_featured=True, is_published=True)
    assert article.summary == "Short text" and article.is_featured and kb.render_body(article.body)[1] == [("hi", "Hi")]


def test_the_starter_articles_are_added_once_and_every_one_renders():
    from django.core.management import call_command

    call_command("seed_help")
    first = KBArticle.objects.count()
    call_command("seed_help")  # running it again changes nothing
    assert KBArticle.objects.count() == first
    from apps.support.kb_seed import ARTICLES

    for article in KBArticle.objects.filter(title__in=[row[1] for row in ARTICLES]):
        html, _ = kb.render_body(article.body)
        assert "<script" not in html and article.summary
    assert KBCategory.objects.filter(slug="getting-started").exists() and KBArticle.objects.filter(slug="how-to-create-your-first-website").exists()


def test_there_are_over_a_hundred_detailed_articles_and_every_link_between_them_works(db):
    import re

    from django.core.management import call_command

    from apps.support.kb_seed import ARTICLES, CATEGORIES

    call_command("seed_help")
    assert len(ARTICLES) >= 100 and len(CATEGORIES) == 9
    assert sum(len(a[4].split()) for a in ARTICLES) / len(ARTICLES) > 200  # detailed, not a few lines each
    titles = {a[1].lower() for a in ARTICLES}
    for category, title, summary, featured, body in ARTICLES:
        assert 40 <= len(summary) <= 160, title  # a search engine shows about this much
        assert len(kb.faq_of(body)) >= 3, title  # every article ends with real questions
        for link in re.findall(r"\[\[([^\]\n]+)\]\]", body):
            assert link.strip().lower() in titles, (title, link)
    assert KBArticle.objects.filter(is_published=True).count() >= 100
    html = kb.render_body(KBArticle.objects.get(slug="how-to-fix-a-500-internal-server-error").body)[0]
    assert 'href="/help/hosting-tools/how-to-read-the-error-log-of-your-website/"' in html  # [[Title]] became a real link


def test_an_article_page_carries_what_search_engines_read(client, library):
    first, second, a, b = library
    KBArticle.objects.filter(pk=a.pk).update(body=a.body + "\n\n## Frequently asked questions\n\n### Does it cost anything?\nNo, it is free.\n\n### How long does it take?\nA minute.")
    html = client.get(reverse("help:article", args=["library-one", "set-up-the-thing"])).content.decode()
    assert "<title>Set up the thing | " in html and 'name="description" content="The quick way."' in html
    assert 'rel="canonical" href="' in html and '/help/library-one/set-up-the-thing/"' in html and 'property="og:title" content="Set up the thing"' in html
    assert 'application/ld+json' in html and '"@type": "Article"' in html and '"@type": "BreadcrumbList"' in html and '"@type": "FAQPage"' in html and "Does it cost anything?" in html
    assert "<script" not in html.split('<script type="application/ld+json">')[1].split("</script>")[0]  # nothing can close the data block early
    assert 'content="noindex' not in html
    assert 'content="noindex, follow"' in client.get(reverse("help:index") + "?q=mailbox").content.decode()  # search result pages are not indexed
    assert 'rel="canonical"' in client.get(reverse("help:category", args=["library-one"])).content.decode()


def test_the_sitemap_lists_every_published_article_and_robots_points_to_it(client, library):
    sitemap = client.get("/sitemap.xml")
    xml = sitemap.content.decode()
    assert sitemap.status_code == 200 and sitemap["Content-Type"].startswith("application/xml")
    assert "/help/library-one/set-up-the-thing/" in xml and "/help/library-one/" in xml and "<lastmod>" in xml
    assert "draft-thing" not in xml and "library-empty" not in xml
    robots = client.get("/robots.txt")
    text = robots.content.decode()
    assert robots.status_code == 200 and "Disallow: /account/" in text and "Disallow: /staff/" in text and "Sitemap: " in text and text.rstrip().endswith("/sitemap.xml")


def test_faq_pairs_are_read_from_the_questions_section_only():
    body = "Intro.\n\n## Steps\n\n### Not a question\nIgnore.\n\n## Frequently asked questions\n\n### Is it free?\nYes, **free**.\n\n### Can I stop?\nAnytime.\n\n## After\n\nMore."
    assert kb.faq_of(body) == [("Is it free?", "Yes, free."), ("Can I stop?", "Anytime.")]


def test_the_questions_become_openable_answers_and_the_page_says_what_it_is(client, library):
    first, second, a, b = library
    KBArticle.objects.filter(pk=a.pk).update(body=a.body + "\n\n## Frequently asked questions\n\n### Does it cost anything?\nNo, it is **free**.")
    html = client.get(reverse("help:article", args=["library-one", "set-up-the-thing"])).content.decode()
    assert '<details class="help-faq"><summary>Does it cost anything?</summary><div><p>No, it is <strong>free</strong>.</p></div></details>' in html
    assert 'class="help-banner"' in html and "min read" in html and "Updated" in html and "question" in html and 'class="help-steps"' in html
    assert 'class="help-topic-list"' in html and 'aria-current="page">Set up the thing' in html  # the topic list shows where you are


def test_a_reader_can_say_whether_an_article_helped_once_and_staff_keep_the_count(client, library):
    first, second, a, b = library
    url = reverse("help:vote", args=["library-one", "set-up-the-thing"])
    assert client.get(url).status_code == 404
    assert client.post(url, {"choice": "maybe"}).status_code == 400
    assert client.post(url, {"choice": "yes"}).json() == {"ok": True, "choice": "yes"}
    client.post(url, {"choice": "no"})  # the same reader again: not counted twice, the first answer stays
    a.refresh_from_db()
    assert (a.helpful_yes, a.helpful_no) == (1, 0)
    other = type(client)()
    other.post(url, {"choice": "no"})
    a.refresh_from_db()
    assert (a.helpful_yes, a.helpful_no) == (1, 1)
    assert 'data-voted="yes"' in client.get(reverse("help:article", args=["library-one", "set-up-the-thing"])).content.decode()
    assert client.post(reverse("help:vote", args=["library-one", "draft-thing"]), {"choice": "yes"}).status_code == 404
