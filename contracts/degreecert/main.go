package main

import (
	"encoding/json"
	"fmt"
	"os"

	"degreecert/core"

	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
	"chainmaker.org/chainmaker/contract-sdk-go/v2/sandbox"
	"chainmaker.org/chainmaker/contract-sdk-go/v2/sdk"
)

type CertContract struct{}

type chainStore struct{ client sdk.SDKInterface }

func (s chainStore) Get(key, field string) ([]byte, error) {
	return s.client.GetStateByte(key, field)
}

func (s chainStore) Put(key, field string, value []byte) error {
	return s.client.PutStateByte(key, field, value)
}

// All successful contract responses use one JSON envelope. Contract errors must
// use sdk.Error, so both the CMC adapter and the chain see a failed execution.
func response(data interface{}, err error) protogo.Response {
	if err != nil {
		return sdk.Error(err.Error())
	}
	payload, err := json.Marshal(struct {
		OK   bool        `json:"ok"`
		Data interface{} `json:"data"`
	}{OK: true, Data: data})
	if err != nil {
		return sdk.Error("encode response: " + err.Error())
	}
	return sdk.Success(payload)
}

func (c *CertContract) InitContract() protogo.Response {
	if sdk.Instance == nil {
		return sdk.Error("SDK_NOT_INITIALIZED")
	}
	args := sdk.Instance.GetArgs()
	service := core.New(chainStore{client: sdk.Instance})
	return response(service.Init(string(args["issuer_org"]), string(args["regulator_org"])))
}

// Chain-level contract management policies authorize upgrades. An upgrade never
// silently replaces the organizations or reinitializes existing records.
func (c *CertContract) UpgradeContract() protogo.Response {
	if sdk.Instance == nil {
		return sdk.Error("SDK_NOT_INITIALIZED")
	}
	return response(core.New(chainStore{client: sdk.Instance}).Health())
}

func (c *CertContract) InvokeContract(method string) protogo.Response {
	if sdk.Instance == nil {
		return sdk.Error("SDK_NOT_INITIALIZED")
	}
	service := core.New(chainStore{client: sdk.Instance})
	args := sdk.Instance.GetArgs()
	switch method {
	case "health":
		return response(service.Health())
	case "getCertificate":
		return response(service.Get(string(args["cert_id"])))
	case "verifyCertificate":
		return response(service.Verify(string(args["cert_id"]), string(args["content_hash"])))
	case "issueCertificate", "revokeCertificate":
		ctx, err := transactionContext(sdk.Instance)
		if err != nil {
			return sdk.Error(err.Error())
		}
		if method == "issueCertificate" {
			return response(service.Issue(ctx, string(args["cert_id"]), string(args["content_hash"])))
		}
		return response(service.Revoke(ctx, string(args["cert_id"]), string(args["reason"])))
	default:
		return sdk.Error("UNKNOWN_METHOD")
	}
}

func transactionContext(client sdk.SDKInterface) (core.Context, error) {
	org, err := client.GetSenderOrgId()
	if err != nil {
		return core.Context{}, fmt.Errorf("get sender organization: %w", err)
	}
	role, err := client.GetSenderRole()
	if err != nil {
		return core.Context{}, fmt.Errorf("get sender role: %w", err)
	}
	txID, err := client.GetTxId()
	if err != nil {
		return core.Context{}, fmt.Errorf("get transaction ID: %w", err)
	}
	timestamp, err := client.GetTxTimeStamp()
	if err != nil {
		return core.Context{}, fmt.Errorf("get transaction timestamp: %w", err)
	}
	return core.Context{SenderOrg: org, SenderRole: role, TxID: txID, Timestamp: timestamp}, nil
}

func main() {
	if err := sandbox.Start(new(CertContract)); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
