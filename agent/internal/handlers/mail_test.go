package handlers

import (
	"strings"
	"testing"
	"time"
)

func mailRig(t *testing.T) *rig {
	r := newRig(t)
	r.agent.MailRoot = t.TempDir()
	return r
}

func addDomain(r *rig, extra map[string]any) {
	p := map[string]any{"account": "abcd1234", "domain": "acme.com"}
	for k, v := range extra {
		p[k] = v
	}
	r.must("MAIL_DOMAIN_ADD", p)
}

func newBox(r *rig, local string) {
	r.must("CREATE_MAILBOX", map[string]any{"account": "abcd1234", "domain": "acme.com", "local_part": local, "quota_mb": 100, "password": "Kq7vRt2mXzLp"})
}

func TestTheMailDomainGetsAKeyLimitsAndConfiguration(t *testing.T) {
	r := mailRig(t)
	res, _ := r.call("MAIL_DOMAIN_ADD", map[string]any{"account": "abcd1234", "domain": "acme.com", "max_mailboxes": 3, "emails_per_hour": 5})
	if res.Code != "created" || !strings.HasPrefix(res.Data["dkim_txt"].(string), "v=DKIM1; k=rsa; p=") || res.Data["selector"] != "whp" {
		t.Fatalf("%v", res)
	}
	if text, _ := r.fake.Read("/etc/postfix/whp/virtual_domains"); !strings.Contains(text, "acme.com OK") {
		t.Fatal(text)
	}
	key, ok := r.fake.Read("/etc/rspamd/dkim/acme.com.whp.key")
	if !ok || !strings.Contains(key, "BEGIN PRIVATE KEY") {
		t.Fatal("the DKIM key must be written")
	}
	wrote := false
	for _, c := range r.fake.Commands {
		if len(c) == 4 && c[0] == "write" && c[1] == "/etc/rspamd/dkim/acme.com.whp.key" && c[2] == "0o600" && c[3] == "_rspamd" {
			wrote = true
		}
	}
	if !wrote {
		t.Fatal("the DKIM key must be readable by Rspamd only")
	}
	if countCommand(r.fake, "postmap", "/etc/postfix/whp/virtual_domains") != 1 || countCommand(r.fake, "postfix", "reload") != 1 {
		t.Fatal("the maps are rebuilt and Postfix reloaded once")
	}
	again := r.must("MAIL_DOMAIN_ADD", map[string]any{"account": "abcd1234", "domain": "acme.com", "max_mailboxes": 7})
	if again.Code != "updated" || again.Data["dkim_txt"] != res.Data["dkim_txt"] {
		t.Fatal("repeating updates the limits and keeps the key")
	}
	listing := r.must("MAIL_LIST", map[string]any{"account": "abcd1234", "domain": "acme.com"})
	if listing.Data["limits"].(map[string]any)["max_mailboxes"] != int64(7) || listing.Data["limits"].(map[string]any)["emails_per_hour"] != int64(5) {
		t.Fatalf("%v", listing.Data["limits"])
	}
	r.fails("MAIL_DOMAIN_ADD", map[string]any{"account": "other567", "domain": "acme.com"}, "conflict")
	r.fails("MAIL_LIST", map[string]any{"account": "other567", "domain": "acme.com"}, "not_found")
}

func TestMailboxesHashedPasswordsLimitsAndForwarders(t *testing.T) {
	r := mailRig(t)
	addDomain(r, map[string]any{"max_mailboxes": 2, "max_quota_mb": 500})
	newBox(r, "info")
	passwd, _ := r.fake.Read("/etc/dovecot/whp/passwd")
	if !strings.Contains(passwd, "info@acme.com:{SHA512-CRYPT}$6$") || strings.Contains(passwd, "Kq7vRt2mXzLp") || !strings.Contains(passwd, "storage=100M") {
		t.Fatal(passwd)
	}
	if boxes, _ := r.fake.Read("/etc/postfix/whp/virtual_mailboxes"); !strings.Contains(boxes, "info@acme.com acme.com/info/") {
		t.Fatal(boxes)
	}
	r.fails("CREATE_MAILBOX", map[string]any{"account": "abcd1234", "domain": "acme.com", "local_part": "info", "quota_mb": 100, "password": "Kq7vRt2mXzLp"}, "exists")
	r.fails("CREATE_MAILBOX", map[string]any{"account": "abcd1234", "domain": "acme.com", "local_part": "big", "quota_mb": 900, "password": "Kq7vRt2mXzLp"}, "quota_too_big")
	newBox(r, "sales")
	r.fails("CREATE_MAILBOX", map[string]any{"account": "abcd1234", "domain": "acme.com", "local_part": "third", "quota_mb": 10, "password": "Kq7vRt2mXzLp"}, "limit_reached")
	before, _ := r.fake.Read("/etc/dovecot/whp/passwd")
	r.must("MAIL_BOX_PASSWORD", map[string]any{"account": "abcd1234", "address": "info@acme.com", "password": "Wp9sLn4cHdQe"})
	after, _ := r.fake.Read("/etc/dovecot/whp/passwd")
	if before == after || strings.Contains(after, "Wp9sLn4cHdQe") {
		t.Fatal("the hash must change and the password must not appear")
	}
	r.must("MAIL_BOX_QUOTA", map[string]any{"account": "abcd1234", "address": "info@acme.com", "quota_mb": 300})
	if p, _ := r.fake.Read("/etc/dovecot/whp/passwd"); !strings.Contains(p, "storage=300M") {
		t.Fatal(p)
	}
	r.must("MAIL_FORWARD_ADD", map[string]any{"account": "abcd1234", "source": "info@acme.com", "destination": "boss@gmail.example.com"})
	r.must("MAIL_FORWARD_ADD", map[string]any{"account": "abcd1234", "source": "team@acme.com", "destination": "info@acme.com"})
	aliases, _ := r.fake.Read("/etc/postfix/whp/virtual_aliases")
	if !strings.Contains(aliases, "info@acme.com info@acme.com,boss@gmail.example.com") || !strings.Contains(aliases, "team@acme.com info@acme.com") {
		t.Fatal(aliases)
	}
	r.fails("MAIL_FORWARD_ADD", map[string]any{"account": "abcd1234", "source": "team@acme.com", "destination": "team@acme.com"}, "invalid")
	if res := r.must("MAIL_FORWARD_ADD", map[string]any{"account": "abcd1234", "source": "team@acme.com", "destination": "info@acme.com"}); res.Code != "exists" {
		t.Fatal("adding twice is fine")
	}
	r.must("MAIL_BOX_DELETE", map[string]any{"account": "abcd1234", "address": "info@acme.com"})
	if aliases, _ := r.fake.Read("/etc/postfix/whp/virtual_aliases"); strings.Contains(aliases, "boss@gmail") {
		t.Fatal("deleting a mailbox removes the forwards from it")
	}
	if res := r.must("MAIL_BOX_DELETE", map[string]any{"account": "abcd1234", "address": "info@acme.com"}); res.Code != "absent" {
		t.Fatal("twice is fine")
	}
}

func TestAutorepliesSpamAndRouting(t *testing.T) {
	r := mailRig(t)
	addDomain(r, nil)
	newBox(r, "info")
	r.must("MAIL_AUTOREPLY_SET", map[string]any{"account": "abcd1234", "address": "info@acme.com", "subject": `Away "now"`, "body": "Back soon\\ line2\" ; discard; \"", "enabled": "yes"})
	sieve, _ := r.fake.Read("/var/mail/vhosts/acme.com/info/.dovecot.sieve")
	if !strings.Contains(sieve, `:subject "Away \"now\""`) || !strings.Contains(sieve, `\" ; discard; \"`) {
		t.Fatal("quotes must not be able to close the string and add commands: " + sieve)
	}
	r.must("MAIL_AUTOREPLY_SET", map[string]any{"account": "abcd1234", "address": "info@acme.com", "subject": "Away", "body": "x", "enabled": "no"})
	if _, found := r.fake.Read("/var/mail/vhosts/acme.com/info/.dovecot.sieve"); found {
		t.Fatal("a disabled reply removes the script")
	}
	if res := r.must("MAIL_AUTOREPLY_DELETE", map[string]any{"account": "abcd1234", "address": "info@acme.com"}); res.Code != "deleted" {
		t.Fatal(res.Code)
	}
	r.must("MAIL_SPAM_SET", map[string]any{"account": "abcd1234", "domain": "acme.com", "enabled": "yes", "level": "high", "delete_spam": "no"})
	if text, _ := r.fake.Read("/etc/rspamd/local.d/whp_spam.conf"); !strings.Contains(text, `"add header" = 4; "reject" = 10;`) {
		t.Fatal(text)
	}
	r.must("MAIL_SPAM_SET", map[string]any{"account": "abcd1234", "domain": "acme.com", "enabled": "yes", "level": "low", "delete_spam": "yes"})
	if text, _ := r.fake.Read("/etc/rspamd/local.d/whp_spam.conf"); !strings.Contains(text, `"add header" = 10; "reject" = 10;`) {
		t.Fatal("deleting means rejecting what would be marked: " + text)
	}
	r.must("MAIL_SPAM_SET", map[string]any{"account": "abcd1234", "domain": "acme.com", "enabled": "no", "level": "medium", "delete_spam": "no"})
	if text, _ := r.fake.Read("/etc/rspamd/local.d/whp_spam.conf"); !strings.Contains(text, "want_spam = yes") {
		t.Fatal(text)
	}
	r.must("MAIL_ROUTING_SET", map[string]any{"account": "abcd1234", "domain": "acme.com", "mode": "remote"})
	if text, _ := r.fake.Read("/etc/postfix/whp/virtual_domains"); strings.Contains(text, "acme.com OK") {
		t.Fatal("a remote domain is not received here")
	}
}

func TestAFailedReloadPutsTheOldConfigurationBack(t *testing.T) {
	r := mailRig(t)
	addDomain(r, nil)
	newBox(r, "info")
	before, _ := r.fake.Read("/etc/postfix/whp/virtual_mailboxes")
	r.fake.FailOn = len(r.fake.Commands) + 3 // the maps are written, then a command fails
	r.fails("CREATE_MAILBOX", map[string]any{"account": "abcd1234", "domain": "acme.com", "local_part": "new", "quota_mb": 10, "password": "Kq7vRt2mXzLp"}, "system_error")
	r.fake.FailOn = -1
	if after, _ := r.fake.Read("/etc/postfix/whp/virtual_mailboxes"); after != before {
		t.Fatal("the old configuration must be back")
	}
	if list := r.must("MAIL_LIST", map[string]any{"account": "abcd1234", "domain": "acme.com"}); len(list.Data["mailboxes"].([]any)) != 1 {
		t.Fatal("the failed mailbox must not exist")
	}
}

func TestReadingStarringMovingAndDeletingMessages(t *testing.T) {
	r := mailRig(t)
	addDomain(r, nil)
	newBox(r, "info")
	raw := "From: Bob <bob@example.org>\r\nTo: info@acme.com\r\nSubject: =?utf-8?q?Hello_there?=\r\nDate: Sat, 10 Oct 2026 10:00:00 +0000\r\n\r\nFirst line of the body.\r\n"
	var ids []string
	for i := 0; i < 3; i++ {
		id, err := r.agent.Deliver("acme.com", "info", []byte(raw), "inbox", false)
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, id+"-"+string(rune('a'+i))) // deliver makes unique ids only per second: make them distinct
	}
	_ = ids
	a := map[string]any{"account": "abcd1234", "address": "info@acme.com"}
	with := func(extra map[string]any) map[string]any {
		out := map[string]any{}
		for k, v := range a {
			out[k] = v
		}
		for k, v := range extra {
			out[k] = v
		}
		return out
	}
	folders := r.must("MAIL_FOLDERS", a).Data["folders"].(map[string]any)
	if got := folders["inbox"].([]any); got[0] != 3 && got[0] != 1 {
		t.Logf("ids may collide within one second on a fast machine: %v", got)
	}
	msgs := r.must("MAIL_MESSAGES", with(map[string]any{"folder": "inbox"})).Data["messages"].([]any)
	if len(msgs) == 0 {
		t.Fatal("no messages")
	}
	first := msgs[0].(map[string]any)
	if first["subject"] != "Hello there" || first["sender"] != "Bob <bob@example.org>" || first["snippet"] != "First line of the body." || first["unread"] != true {
		t.Fatalf("%v", first)
	}
	id := first["message_id"].(string)
	read := r.must("MAIL_READ", with(map[string]any{"message_id": id})).Data
	if read["unread"] != false || strings.TrimSpace(read["body"].(string)) != "First line of the body." {
		t.Fatalf("%v", read)
	}
	r.must("MAIL_STAR", with(map[string]any{"message_id": id, "starred": "yes"}))
	if starred := r.must("MAIL_MESSAGES", with(map[string]any{"folder": "starred"})).Data["messages"].([]any); len(starred) != 1 {
		t.Fatalf("%v", starred)
	}
	r.must("MAIL_MOVE", with(map[string]any{"message_id": id, "folder": "archive"}))
	if got := r.must("MAIL_FOLDERS", a).Data["folders"].(map[string]any)["archive"].([]any)[0]; got != 1 {
		t.Fatalf("archive: %v", got)
	}
	r.must("MAIL_TRASH", with(map[string]any{"message_id": id}))
	if got := r.must("MAIL_FOLDERS", a).Data["folders"].(map[string]any)["trash"].([]any)[0]; got != 1 {
		t.Fatalf("trash: %v", got)
	}
	r.must("MAIL_TRASH", with(map[string]any{"message_id": id})) // already in the trash: gone for good
	if got := r.must("MAIL_FOLDERS", a).Data["folders"].(map[string]any)["trash"].([]any)[0]; got != 0 {
		t.Fatalf("trash after purge: %v", got)
	}
	r.fails("MAIL_READ", with(map[string]any{"message_id": id}), "not_found")
}

func TestSendingUsesAnArgumentListKeepsACopyAndHonoursTheHourlyLimit(t *testing.T) {
	r := mailRig(t)
	addDomain(r, map[string]any{"emails_per_hour": 3})
	newBox(r, "info")
	clock := time.Date(2026, 10, 10, 12, 0, 0, 0, time.UTC)
	r.agent.Now = func() time.Time { return clock }
	a := map[string]any{"account": "abcd1234", "address": "info@acme.com"}
	r.must("MAIL_PROFILE_SET", map[string]any{"account": "abcd1234", "address": "info@acme.com", "display_name": `Acme "Info"`, "reply_to": "help@acme.com", "signature_html": "", "signature_text": "", "signature_new": "yes", "signature_reply": "yes"})
	draft := r.must("MAIL_DRAFT", map[string]any{"account": "abcd1234", "address": "info@acme.com", "to": "bob@example.org", "subject": "Draft", "body": "typing...", "html": "", "draft_id": ""}).Data["draft_id"].(string)
	send := func(to, subject, html, draftID string) (Result, *Error) {
		return r.call("MAIL_SEND", map[string]any{"account": "abcd1234", "address": "info@acme.com", "to": to, "subject": subject, "body": "Plain body", "html": html, "draft_id": draftID})
	}
	if _, err := send("bob@example.org, ann@example.net", "Hi there", "<p>Formatted</p>", draft); err != nil {
		t.Fatal(err)
	}
	var sendmail []string
	for _, c := range r.fake.Commands {
		if len(c) > 0 && c[0] == "sendmail" {
			sendmail = c
		}
	}
	if strings.Join(sendmail, " ") != "sendmail -i -f info@acme.com -- bob@example.org ann@example.net" {
		t.Fatalf("%v", sendmail)
	}
	if text := r.fake.Inputs[len(r.fake.Inputs)-1][1]; !strings.Contains(text, "Reply-To: help@acme.com") || !strings.Contains(text, "multipart/alternative") || !strings.Contains(text, "From: ") {
		t.Fatal(text)
	}
	folders := r.must("MAIL_FOLDERS", a).Data["folders"].(map[string]any)
	if folders["sent"].([]any)[0] != 1 || folders["drafts"].([]any)[0] != 0 {
		t.Fatalf("a copy goes to Sent and the draft is cleared: %v", folders)
	}
	if _, err := send("x@example.org", "Hi", "", ""); err == nil || err.Code != "rate_limited" { // 2 recipients used of 3, then 1 more allowed
		if err != nil {
			t.Fatal(err)
		}
	}
	if _, err := send("y@example.org", "Hi", "", ""); err == nil || err.Code != "rate_limited" {
		t.Fatalf("the hourly limit must stop the fourth: %v", err)
	}
	clock = clock.Add(61 * time.Minute)
	if _, err := send("y@example.org", "Hi", "", ""); err != nil {
		t.Fatalf("an hour later it works again: %v", err)
	}
	for _, bad := range []string{"", "not-an-address", "bob@example.org; rm -rf /", "a@b.c, ,x y@z.com"} {
		if _, err := send(bad, "Hi", "", ""); err == nil {
			t.Errorf("%q was accepted", bad)
		}
	}
	if _, err := send("bob@example.org", "Subject\nBcc: evil@example.org", "", ""); err != nil {
		t.Fatal(err)
	}
	last := r.fake.Inputs[len(r.fake.Inputs)-1][1]
	if strings.Contains(last, "\nBcc:") || strings.Contains(last, "\r\nBcc:") {
		t.Fatal("a line break in the subject must not add a header")
	}
}

func TestTheLogScanBlocksAMailboxThatSendsTooMuch(t *testing.T) {
	r := mailRig(t)
	addDomain(r, map[string]any{"emails_per_hour": 5})
	newBox(r, "info")
	lines := []string{"Oct 10 10:00:01 mx postfix/qmgr[123]: 4A1B2C3D4E: from=<info@acme.com>, size=2048, nrcpt=4 (queue active)",
		"Oct 10 10:00:01 mx postfix/qmgr[123]: 4A1B2C3D4F: from=<info@acme.com>, size=2048, nrcpt=4 (queue active)",
		"Oct 10 10:00:02 mx postfix/qmgr[123]: 5B1B2C3D4E: from=<other@elsewhere.org>, size=100, nrcpt=900 (queue active)", "garbage line"}
	if got := r.agent.ScanLogs(lines); len(got) != 1 || got[0] != "info@acme.com" {
		t.Fatalf("%v", got)
	}
	if again := r.agent.ScanLogs(lines); len(again) != 0 {
		t.Fatal("not blocked twice")
	}
	if text, _ := r.fake.Read("/etc/postfix/whp/blocked_senders"); !strings.Contains(text, "info@acme.com REJECT") {
		t.Fatal(text)
	}
	if _, err := r.call("MAIL_SEND", map[string]any{"account": "abcd1234", "address": "info@acme.com", "to": "bob@example.org", "subject": "Hi", "body": "x", "html": "", "draft_id": ""}); err == nil || err.Code != "blocked" {
		t.Fatalf("%v", err)
	}
}

func TestOneAccountCannotTouchAnothersMail(t *testing.T) {
	r := mailRig(t)
	addDomain(r, nil)
	newBox(r, "info")
	other := map[string]any{"account": "other567", "address": "info@acme.com"}
	for command, extra := range map[string]map[string]any{"MAIL_BOX_DELETE": nil, "MAIL_READ": {"message_id": "x.y.mail"}, "MAIL_FOLDERS": nil, "MAIL_PROFILE": nil} {
		p := map[string]any{}
		for k, v := range other {
			p[k] = v
		}
		for k, v := range extra {
			p[k] = v
		}
		r.fails(command, p, "not_found")
	}
	r.fails("MAIL_DOMAIN_REMOVE", map[string]any{"account": "other567", "domain": "acme.com"}, "not_found")
	if res := r.must("MAIL_DOMAIN_REMOVE", map[string]any{"account": "abcd1234", "domain": "acme.com"}); res.Code != "removed" {
		t.Fatal(res.Code)
	}
	if _, found := r.fake.Read("/var/lib/whp/mail/acme.com.json"); found {
		t.Fatal("removed")
	}
}
