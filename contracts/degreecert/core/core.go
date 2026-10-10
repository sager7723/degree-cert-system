// Package core implements deterministic certificate rules independently of the VM.
package core

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	SchemaVersion  = "degree-cert/v1"
	StatusValid    = "valid"
	StatusRevoked  = "revoked"
	MaxReasonRunes = 256
	configKey      = "degreecert"
	configField    = "config"
	certificateKey = "certificate"
)

var (
	ErrAlreadyInitialized = errors.New("ALREADY_INITIALIZED")
	ErrNotInitialized     = errors.New("NOT_INITIALIZED")
	ErrUnauthorized       = errors.New("UNAUTHORIZED")
	ErrAlreadyExists      = errors.New("CERTIFICATE_ALREADY_EXISTS")
	ErrNotFound           = errors.New("CERTIFICATE_NOT_FOUND")
	ErrAlreadyRevoked     = errors.New("CERTIFICATE_ALREADY_REVOKED")
	certIDPattern         = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`)
	orgIDPattern          = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)
	hashPattern           = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// Store values must be empty only when the key/field does not exist.
// ChainMaker transaction execution provides the atomic commit and conflict checks.
type Store interface {
	Get(key, field string) ([]byte, error)
	Put(key, field string, value []byte) error
}

type Config struct {
	SchemaVersion string `json:"schema_version"`
	IssuerOrg     string `json:"issuer_org"`
	RegulatorOrg  string `json:"regulator_org"`
}

// Context is populated by the SDK, never from user-supplied parameters.
// Timestamp is the transaction timestamp (Unix seconds), not local wall-clock time.
type Context struct {
	SenderOrg  string
	SenderRole string
	TxID       string
	Timestamp  string
}

// Certificate contains a digest and ledger metadata, not student information.
type Certificate struct {
	SchemaVersion string `json:"schema_version"`
	CertID        string `json:"cert_id"`
	ContentHash   string `json:"content_hash"`
	Status        string `json:"status"`
	IssuerOrg     string `json:"issuer_org"`
	IssuedTxID    string `json:"issued_txid"`
	IssuedAt      string `json:"issued_at"`
	RevokedByOrg  string `json:"revoked_by_org,omitempty"`
	RevokedTxID   string `json:"revoked_txid,omitempty"`
	RevokedAt     string `json:"revoked_at,omitempty"`
	RevokeReason  string `json:"revoke_reason,omitempty"`
}

type Health struct {
	Contract      string `json:"contract"`
	Initialized   bool   `json:"initialized"`
	SchemaVersion string `json:"schema_version"`
	IssuerOrg     string `json:"issuer_org"`
	RegulatorOrg  string `json:"regulator_org"`
}

type Verification struct {
	SchemaVersion string `json:"schema_version"`
	CertID        string `json:"cert_id"`
	ContentHash   string `json:"content_hash"`
	ContentMatch  bool   `json:"content_match"`
	Status        string `json:"status"`
	Valid         bool   `json:"valid"`
}

type Service struct{ store Store }

func New(store Store) *Service { return &Service{store: store} }

func (s *Service) Init(issuerOrg, regulatorOrg string) (*Config, error) {
	raw, err := s.store.Get(configKey, configField)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	if len(raw) != 0 {
		return nil, ErrAlreadyInitialized
	}
	if !orgIDPattern.MatchString(issuerOrg) || !orgIDPattern.MatchString(regulatorOrg) {
		return nil, errors.New("INVALID_ORG: use 1-128 ASCII letters, digits, underscores, dots or hyphens; start with a letter or digit")
	}
	if issuerOrg == regulatorOrg {
		return nil, errors.New("INVALID_ORG: issuer and regulator must be different organizations")
	}
	cfg := &Config{SchemaVersion: SchemaVersion, IssuerOrg: issuerOrg, RegulatorOrg: regulatorOrg}
	if err := s.write(configKey, configField, cfg); err != nil {
		return nil, err
	}
	return cfg, nil
}

func (s *Service) loadConfig() (*Config, error) {
	raw, err := s.store.Get(configKey, configField)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	if len(raw) == 0 {
		return nil, ErrNotInitialized
	}
	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return nil, fmt.Errorf("invalid stored config: %w", err)
	}
	if cfg.SchemaVersion != SchemaVersion || !orgIDPattern.MatchString(cfg.IssuerOrg) ||
		!orgIDPattern.MatchString(cfg.RegulatorOrg) || cfg.IssuerOrg == cfg.RegulatorOrg {
		return nil, errors.New("INVALID_STORED_CONFIG")
	}
	return &cfg, nil
}

func (s *Service) Health() (*Health, error) {
	cfg, err := s.loadConfig()
	if err != nil {
		return nil, err
	}
	return &Health{Contract: "degreecert", Initialized: true, SchemaVersion: cfg.SchemaVersion,
		IssuerOrg: cfg.IssuerOrg, RegulatorOrg: cfg.RegulatorOrg}, nil
}

func (s *Service) Issue(ctx Context, certID, contentHash string) (*Certificate, error) {
	cfg, err := s.loadConfig()
	if err != nil {
		return nil, err
	}
	if ctx.SenderOrg != cfg.IssuerOrg || ctx.SenderRole != "CLIENT" {
		return nil, ErrUnauthorized
	}
	if err := validateContext(ctx); err != nil {
		return nil, err
	}
	if err := validateCertID(certID); err != nil {
		return nil, err
	}
	if err := validateHash(contentHash); err != nil {
		return nil, err
	}
	raw, err := s.store.Get(certificateKey, certID)
	if err != nil {
		return nil, fmt.Errorf("read certificate: %w", err)
	}
	if len(raw) != 0 {
		return nil, ErrAlreadyExists
	}
	cert := &Certificate{SchemaVersion: SchemaVersion, CertID: certID, ContentHash: contentHash,
		Status: StatusValid, IssuerOrg: ctx.SenderOrg, IssuedTxID: ctx.TxID, IssuedAt: ctx.Timestamp}
	if err := s.write(certificateKey, certID, cert); err != nil {
		return nil, err
	}
	return cert, nil
}

func (s *Service) Get(certID string) (*Certificate, error) {
	if err := validateCertID(certID); err != nil {
		return nil, err
	}
	if _, err := s.loadConfig(); err != nil {
		return nil, err
	}
	raw, err := s.store.Get(certificateKey, certID)
	if err != nil {
		return nil, fmt.Errorf("read certificate: %w", err)
	}
	if len(raw) == 0 {
		return nil, ErrNotFound
	}
	var cert Certificate
	if err := json.Unmarshal(raw, &cert); err != nil {
		return nil, fmt.Errorf("invalid stored certificate: %w", err)
	}
	if cert.CertID != certID || cert.SchemaVersion != SchemaVersion || !hashPattern.MatchString(cert.ContentHash) ||
		(cert.Status != StatusValid && cert.Status != StatusRevoked) {
		return nil, errors.New("INVALID_STORED_CERTIFICATE")
	}
	return &cert, nil
}

func (s *Service) Verify(certID, contentHash string) (*Verification, error) {
	if err := validateHash(contentHash); err != nil {
		return nil, err
	}
	cert, err := s.Get(certID)
	if err != nil {
		return nil, err
	}
	match := cert.ContentHash == contentHash
	return &Verification{SchemaVersion: cert.SchemaVersion, CertID: cert.CertID, ContentHash: cert.ContentHash,
		ContentMatch: match, Status: cert.Status, Valid: match && cert.Status == StatusValid}, nil
}

func (s *Service) Revoke(ctx Context, certID, reason string) (*Certificate, error) {
	cfg, err := s.loadConfig()
	if err != nil {
		return nil, err
	}
	if ctx.SenderOrg != cfg.RegulatorOrg || ctx.SenderRole != "CLIENT" {
		return nil, ErrUnauthorized
	}
	if err := validateContext(ctx); err != nil {
		return nil, err
	}
	if err := validateReason(reason); err != nil {
		return nil, err
	}
	cert, err := s.Get(certID)
	if err != nil {
		return nil, err
	}
	if cert.Status == StatusRevoked {
		return nil, ErrAlreadyRevoked
	}
	cert.Status = StatusRevoked
	cert.RevokedByOrg = ctx.SenderOrg
	cert.RevokedTxID = ctx.TxID
	cert.RevokedAt = ctx.Timestamp
	cert.RevokeReason = reason
	if err := s.write(certificateKey, certID, cert); err != nil {
		return nil, err
	}
	return cert, nil
}

func (s *Service) write(key, field string, value interface{}) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("encode state: %w", err)
	}
	if err := s.store.Put(key, field, raw); err != nil {
		return fmt.Errorf("write state: %w", err)
	}
	return nil
}

func validateCertID(certID string) error {
	if !certIDPattern.MatchString(certID) {
		return errors.New("INVALID_CERT_ID: use 1-64 ASCII letters, digits, underscores or hyphens; start with a letter or digit")
	}
	return nil
}

func validateHash(contentHash string) error {
	if !hashPattern.MatchString(contentHash) {
		return errors.New("INVALID_CONTENT_HASH: expected 64 lowercase hexadecimal SHA-256 characters")
	}
	return nil
}

func validateContext(ctx Context) error {
	if ctx.SenderOrg == "" || ctx.TxID == "" {
		return errors.New("INVALID_TX_CONTEXT: missing sender organization or transaction ID")
	}
	seconds, err := strconv.ParseInt(ctx.Timestamp, 10, 64)
	if err != nil || seconds <= 0 {
		return errors.New("INVALID_TX_CONTEXT: expected positive Unix timestamp in seconds")
	}
	return nil
}

func validateReason(reason string) error {
	if !utf8.ValidString(reason) || strings.TrimSpace(reason) == "" || utf8.RuneCountInString(reason) > MaxReasonRunes {
		return errors.New("INVALID_REASON: expected 1-256 UTF-8 characters with non-whitespace content")
	}
	for _, char := range reason {
		if unicode.IsControl(char) {
			return errors.New("INVALID_REASON: control characters are not permitted")
		}
	}
	return nil
}
