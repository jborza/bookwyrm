"""paste "title\\nauthor" and add the book, optionally as read"""

from datetime import datetime

import dateutil.tz
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Count, Q
from django.template.response import TemplateResponse
from django.utils.decorators import method_decorator
from django.views import View

from bookwyrm import models
from bookwyrm.views.helpers import load_date_in_user_tz_as_utc


def parse_blocks(text):
    """lines are title/author pairs; blank lines are optional separators.
    a group of non-blank lines must have an even number of lines.
    returns (parsed, errors)"""
    groups, current = [], []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip():
            current.append(line.strip())
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    parsed, errors = [], []
    for group in groups:
        if len(group) % 2:
            errors.append(
                f"odd number of lines (need title + author pairs), nothing added for: {group[0]!r} ..."
            )
            continue
        for i in range(0, len(group), 2):
            parsed.append((group[i], group[i + 1]))
    return parsed, errors


def find_author(name):
    """existing author with this name (case-insensitive), most-used first"""
    return (
        models.Author.objects.filter(Q(name__iexact=name) | Q(aliases__contains=[name]))
        .annotate(book_count=Count("book"))
        .order_by("-book_count", "id")
        .first()
    )


def mark_read(user, edition, finish):
    """put edition on the read shelf and add a finished readthrough"""
    read_shelf = user.shelf_set.get(identifier=models.Shelf.READ_FINISHED)
    # only one read-status shelf per book
    models.ShelfBook.objects.filter(
        user=user,
        book=edition,
        shelf__identifier__in=models.Shelf.READ_STATUS_IDENTIFIERS,
    ).exclude(shelf=read_shelf).delete()
    models.ShelfBook.objects.get_or_create(
        user=user, book=edition, shelf=read_shelf
    )
    if not models.ReadThrough.objects.filter(
        user=user, book=edition, finish_date=finish
    ).exists():
        models.ReadThrough.objects.create(
            user=user, book=edition, start_date=finish, finish_date=finish
        )


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required("bookwyrm.edit_book", raise_exception=True), name="dispatch"
)
class QuickAdd(View):
    """add books from pasted text"""

    @staticmethod
    def today(user):
        """today's date in the user's timezone"""
        tz = dateutil.tz.gettz(user.preferred_timezone) or dateutil.tz.UTC
        return datetime.now(tz).date().isoformat()

    def get(self, request):
        """the form"""
        return TemplateResponse(
            request,
            "quick_add.html",
            {"text": "", "as_read": True, "date": self.today(request.user)},
        )

    def post(self, request):
        """create books"""
        text = request.POST.get("text", "")
        as_read = request.POST.get("as_read") == "on"
        date = request.POST.get("date") or self.today(request.user)
        parsed, errors = parse_blocks(text)
        finish = load_date_in_user_tz_as_utc(date, request.user) if as_read else None
        results = []
        with transaction.atomic():
            for title, author_name in parsed:
                author = find_author(author_name)
                author_new = author is None
                if author_new:
                    author = models.Author.objects.create(name=author_name)
                edition = None
                if not author_new:
                    edition = models.Edition.objects.filter(
                        title__iexact=title, authors=author
                    ).first()
                book_new = edition is None
                if book_new:
                    work = models.Work.objects.create(title=title)
                    work.authors.add(author)
                    edition = models.Edition.objects.create(
                        title=title, parent_work=work
                    )
                    edition.authors.add(author)
                if as_read:
                    mark_read(request.user, edition, finish)
                results.append(
                    {
                        "edition": edition,
                        "author": author,
                        "author_new": author_new,
                        "book_new": book_new,
                        "read": as_read,
                    }
                )
        return TemplateResponse(
            request,
            "quick_add.html",
            {
                "text": "" if results and not errors else text,
                "as_read": as_read,
                "date": date,
                "results": results,
                "errors": errors,
            },
        )
