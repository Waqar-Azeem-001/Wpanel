"""Plain forms: validation only. The service layer applies (and re-checks) everything."""
from django import forms

from apps.domains.models import Domain
from apps.hosting.models import HostingAccount

from .models import CannedReply, Department, KBCategory, TicketPriority, TicketStatus
from .services import CUSTOMER_PRIORITIES, assignable_agents


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """One ``<input type=file multiple>`` returning a list of uploaded files (possibly empty)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.log,.csv"}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data if item]
        return [clean_one(data, initial)] if data else []


ATTACH_HELP = "Up to 5 files (images, PDF or text), 5 MB each."


class NewTicketForm(forms.Form):
    department = forms.ModelChoiceField(queryset=Department.objects.none(), empty_label="Choose a department")
    subject = forms.CharField(max_length=200)
    priority = forms.ChoiceField(choices=[(p, TicketPriority(p).label) for p in CUSTOMER_PRIORITIES],
                                 initial=TicketPriority.NORMAL)
    hosting_account = forms.ModelChoiceField(queryset=HostingAccount.objects.none(), required=False,
                                             empty_label="Not about a hosting account", label="Related hosting")
    domain = forms.ModelChoiceField(queryset=Domain.objects.none(), required=False,
                                    empty_label="Not about a domain", label="Related domain")
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 8}), max_length=20000, label="How can we help?")
    files = MultipleFileField(help_text=ATTACH_HELP, label="Attachments")

    def __init__(self, *args, clients=(), staff=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self.fields["hosting_account"].queryset = HostingAccount.objects.filter(client__in=clients)
        self.fields["domain"].queryset = Domain.objects.filter(client__in=clients)
        if staff:
            self.fields["priority"].choices = TicketPriority.choices


class ReplyForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), max_length=20000, label="Your reply")
    files = MultipleFileField(help_text=ATTACH_HELP, label="Attachments")


class StaffReplyForm(ReplyForm):
    internal = forms.BooleanField(required=False, label="Internal note (the customer will not see it)")
    set_status = forms.ChoiceField(required=False, label="Then set status",
                                   choices=[("", "Agent Reply (default)"), *TicketStatus.choices])
    canned = forms.ModelChoiceField(queryset=CannedReply.objects.none(), required=False, empty_label="Insert a predefined reply",
                                    label="Predefined reply")

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .services import canned_replies_for

        self.fields["canned"].queryset = canned_replies_for(department)
        self.fields["body"].required = False  # a predefined reply can supply the text


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=TicketStatus.choices)


class PriorityForm(forms.Form):
    priority = forms.ChoiceField(choices=TicketPriority.choices)


class DepartmentChangeForm(forms.Form):
    department = forms.ModelChoiceField(queryset=Department.objects.filter(is_active=True))


class AssignForm(forms.Form):
    agent = forms.ChoiceField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.agents = {str(a.pk): a for a in assignable_agents()}
        self.fields["agent"].choices = [("", "Unassigned")] + [(pk, a.full_name or a.email) for pk, a in self.agents.items()]

    def selected(self):
        return self.agents.get(self.cleaned_data.get("agent") or "")


class TicketFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(required=False, choices=[("", "All"), ("active", "Not resolved or closed"),
                                                        *TicketStatus.choices])
    department = forms.ModelChoiceField(queryset=Department.objects.all(), required=False, empty_label="All departments")
    priority = forms.ChoiceField(required=False, choices=[("", "Any priority"), *TicketPriority.choices])
    assigned = forms.ChoiceField(required=False, choices=[("", "Anyone"), ("me", "Mine"), ("none", "Unassigned")])


class DepartmentForm(forms.Form):
    name = forms.CharField(max_length=100)
    description = forms.CharField(required=False, max_length=300)
    sort_order = forms.IntegerField(min_value=0, initial=0)
    is_active = forms.BooleanField(required=False, initial=True)
    default_assignee = forms.ChoiceField(required=False, label="Default assignee")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.agents = {str(a.pk): a for a in assignable_agents()}
        self.fields["default_assignee"].choices = [("", "Nobody")] + [(pk, a.full_name or a.email) for pk, a in self.agents.items()]

    def assignee(self):
        return self.agents.get(self.cleaned_data.get("default_assignee") or "")


class CannedReplyForm(forms.Form):
    title = forms.CharField(max_length=100)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}),
                           help_text="You can use {client}, {ticket} and {agent}.")
    department = forms.ModelChoiceField(queryset=Department.objects.all(), required=False,
                                        empty_label="Every department")
    sort_order = forms.IntegerField(min_value=0, initial=0)
    is_active = forms.BooleanField(required=False, initial=True)


class KBCategoryForm(forms.Form):
    name = forms.CharField(max_length=100)
    description = forms.CharField(required=False, max_length=300)
    sort_order = forms.IntegerField(min_value=0, initial=0)
    is_active = forms.BooleanField(required=False, initial=True)


class KBArticleForm(forms.Form):
    category = forms.ModelChoiceField(queryset=KBCategory.objects.all())
    title = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 14}), help_text="Plain text. Blank lines separate paragraphs.")
    sort_order = forms.IntegerField(min_value=0, initial=0)
    is_published = forms.BooleanField(required=False, label="Published")


class KBSearchForm(forms.Form):
    q = forms.CharField(required=False, label="Search the help centre")

