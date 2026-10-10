package core

import (
	"errors"
	"strings"
	"testing"
)

type memoryStore struct {
	values              map[string][]byte
	failRead, failWrite bool
}

func (m *memoryStore) Get(k, f string) ([]byte, error) {
	if m.failRead {
		return nil, errors.New("read failed")
	}
	return append([]byte(nil), m.values[k+"/"+f]...), nil
}
func (m *memoryStore) Put(k, f string, v []byte) error {
	if m.failWrite {
		return errors.New("write failed")
	}
	m.values[k+"/"+f] = append([]byte(nil), v...)
	return nil
}
func fixture(t *testing.T) (*Service, *memoryStore) {
	t.Helper()
	store := &memoryStore{values: map[string][]byte{}}
	service := New(store)
	if _, err := service.Init("wx-org1.chainmaker.org", "wx-org2.chainmaker.org"); err != nil {
		t.Fatal(err)
	}
	return service, store
}
func issuer() Context {
	return Context{SenderOrg: "wx-org1.chainmaker.org", SenderRole: "CLIENT", TxID: "issue-tx", Timestamp: "1790438400"}
}
func regulator() Context {
	return Context{SenderOrg: "wx-org2.chainmaker.org", SenderRole: "CLIENT", TxID: "revoke-tx", Timestamp: "1790438500"}
}

func TestLifecycleAndHashStability(t *testing.T) {
	s, _ := fixture(t)
	digest := strings.Repeat("a", 64)
	issued, err := s.Issue(issuer(), "CERT_A", digest)
	if err != nil || issued.Status != StatusValid {
		t.Fatalf("issue: %+v %v", issued, err)
	}
	matching, err := s.Verify("CERT_A", digest)
	if err != nil || !matching.Valid || !matching.ContentMatch {
		t.Fatalf("verify original: %+v %v", matching, err)
	}
	altered, err := s.Verify("CERT_A", strings.Repeat("b", 64))
	if err != nil || altered.Valid || altered.ContentMatch {
		t.Fatalf("tamper check: %+v %v", altered, err)
	}
	if _, err := s.Issue(issuer(), "CERT_B", strings.Repeat("c", 64)); err != nil {
		t.Fatal(err)
	}
	revoked, err := s.Revoke(regulator(), "CERT_A", "测试撤销")
	if err != nil {
		t.Fatal(err)
	}
	if revoked.ContentHash != digest || revoked.IssuedTxID != "issue-tx" || revoked.RevokedTxID != "revoke-tx" {
		t.Fatal("lost immutable content or provenance")
	}
	after, err := s.Verify("CERT_A", digest)
	if err != nil || !after.ContentMatch || after.Valid || after.Status != StatusRevoked {
		t.Fatalf("verify revoked: %+v %v", after, err)
	}
	other, err := s.Get("CERT_B")
	if err != nil || other.Status != StatusValid {
		t.Fatal("revoking one certificate affected another")
	}
	if _, err := s.Revoke(regulator(), "CERT_A", "again"); !errors.Is(err, ErrAlreadyRevoked) {
		t.Fatalf("repeat revoke: %v", err)
	}
}

func TestAuthorizationUsesTrustedContext(t *testing.T) {
	s, _ := fixture(t)
	hash := strings.Repeat("a", 64)
	for _, ctx := range []Context{regulator(), {SenderOrg: "wx-org3.chainmaker.org", SenderRole: "CLIENT", TxID: "x", Timestamp: "1790438400"}, {SenderOrg: issuer().SenderOrg, SenderRole: "ADMIN", TxID: "x", Timestamp: "1790438400"}} {
		if _, err := s.Issue(ctx, "FORBIDDEN", hash); !errors.Is(err, ErrUnauthorized) {
			t.Fatalf("issue granted to %+v: %v", ctx, err)
		}
	}
	if _, err := s.Issue(issuer(), "CERT_A", hash); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Revoke(issuer(), "CERT_A", "not regulator"); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("revoke granted: %v", err)
	}
	ctx := regulator()
	ctx.SenderRole = "ADMIN"
	if _, err := s.Revoke(ctx, "CERT_A", "not client"); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("admin granted client permission: %v", err)
	}
}

func TestNoOverwriteAndMissingRecords(t *testing.T) {
	s, _ := fixture(t)
	original := strings.Repeat("a", 64)
	if _, err := s.Issue(issuer(), "CERT_A", original); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Issue(issuer(), "CERT_A", strings.Repeat("b", 64)); !errors.Is(err, ErrAlreadyExists) {
		t.Fatal(err)
	}
	cert, _ := s.Get("CERT_A")
	if cert.ContentHash != original {
		t.Fatal("duplicate overwrote hash")
	}
	if _, err := s.Get("ABSENT"); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
	if _, err := s.Verify("ABSENT", original); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
	if _, err := s.Init("evil", "other"); !errors.Is(err, ErrAlreadyInitialized) {
		t.Fatal(err)
	}
}

func TestValidationAndStorageFailures(t *testing.T) {
	s, store := fixture(t)
	digest := strings.Repeat("a", 64)
	for _, id := range []string{"", "../x", "a b", strings.Repeat("x", 65)} {
		if _, err := s.Issue(issuer(), id, digest); err == nil {
			t.Fatalf("accepted ID %q", id)
		}
	}
	for _, hash := range []string{"", strings.Repeat("A", 64), strings.Repeat("a", 63)} {
		if _, err := s.Issue(issuer(), "BAD_HASH", hash); err == nil {
			t.Fatal("accepted malformed hash")
		}
	}
	ctx := issuer()
	ctx.Timestamp = "-1"
	if _, err := s.Issue(ctx, "BAD_TIME", digest); err == nil {
		t.Fatal("accepted invalid time")
	}
	store.failWrite = true
	if _, err := s.Issue(issuer(), "FAIL", digest); err == nil {
		t.Fatal("write failure ignored")
	}
	store.failWrite = false
	if _, err := s.Get("FAIL"); !errors.Is(err, ErrNotFound) {
		t.Fatal("failed write appeared committed")
	}
	if _, err := s.Issue(issuer(), "CERT_A", digest); err != nil {
		t.Fatal(err)
	}
	for _, reason := range []string{"", "   ", "bad\nreason", strings.Repeat("字", 257)} {
		if _, err := s.Revoke(regulator(), "CERT_A", reason); err == nil {
			t.Fatal("accepted invalid reason")
		}
	}
	store.failWrite = true
	if _, err := s.Revoke(regulator(), "CERT_A", "valid reason"); err == nil {
		t.Fatal("revoke write failure ignored")
	}
	store.failWrite = false
	cert, _ := s.Get("CERT_A")
	if cert.Status != StatusValid {
		t.Fatal("failed revoke changed state")
	}
	store.failRead = true
	if _, err := s.Get("CERT_A"); err == nil {
		t.Fatal("read failure ignored")
	}
}
