/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sandbox

import (
	"errors"
	"fmt"
	"sync"
	"time"

	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
	"go.uber.org/zap"
)

type state string

const (
	created state = "created"
	ready   state = "ready"
)

type ContractEngineClient struct {
	logger          *zap.SugaredLogger
	serialLock      sync.Mutex
	rpcClient       protogo.DockerVMRpc_DockerVMCommunicateClient
	state           state
	putTxReqMsgFunc func(msg *protogo.DockerVMMessage, signalNotify func(signal *protogo.DockerVMMessage))
	txFinishMsgChan chan *protogo.DockerVMMessage

	processName  string
	contractName string
}

func newContractEngineClient(rpcClient protogo.DockerVMRpc_DockerVMCommunicateClient, processName, contractName string, logger *zap.SugaredLogger) *ContractEngineClient {
	return &ContractEngineClient{
		logger:          logger,
		serialLock:      sync.Mutex{},
		rpcClient:       rpcClient,
		txFinishMsgChan: make(chan *protogo.DockerVMMessage, 200),
		state:           created,
		processName:     processName,
		contractName:    contractName,
	}
}

func (c *ContractEngineClient) Start() error {
	c.logger.Debugf("*** contract engine client started ***")
	defer func() {
		err := c.rpcClient.CloseSend()
		if err != nil {
			return
		}
	}()

	// chat with engine
	if err := c.chatWithEngine(); err != nil {
		return err
	}

	errCh := make(chan error, 1)

	// listen incoming message
	go func() {
		if err := c.listenTxRequest(); err != nil {
			errCh <- err
		}
	}()

	// send signal
	go func() {
		if err := c.sendTxFinishMsg(); err != nil {
			errCh <- err
		}
	}()

	return <-errCh
}

func (c *ContractEngineClient) chatWithEngine() error {
	// Send the register
	if err := c.sendMessage(
		&protogo.DockerVMMessage{
			Type: protogo.DockerVMType_REGISTER,
			CrossContext: &protogo.CrossContext{
				ProcessName: c.processName,
			},
		},
	); err != nil {
		return fmt.Errorf("error sending chaincode REGISTER: %s", err)
	}
	c.state = ready

	return nil
}

// listenTxRequest listen request from contract engine
func (c *ContractEngineClient) listenTxRequest() error {
	// holds return values from gRPC Recv below
	// recv message
	for {
		msg, err := c.rpcClient.Recv()
		switch {
		case err != nil:
			err := fmt.Errorf("receive error from contract engine: %s", err)
			return err
		case msg == nil:
			err := errors.New("receive nil message, ending chaincode stream")
			return err
		default:
			c.logger.Debugf("[%s] receive tx request from contract engine, type [%s]", msg.TxId, msg.Type)
			EnterNextStep(msg, protogo.StepType_SANDBOX_GRPC_RECEIVE_TX_REQUEST, "")
			c.putTxReqMsgFunc(msg, c.PutFinishMsg)
		}
	}
}

// RegisterTxRequestPutFunc register put func to send txRequest to txHandler with a callback fun that returns a finish signal
func (c *ContractEngineClient) RegisterTxRequestPutFunc(txRequestPutFunc func(msg *protogo.DockerVMMessage,
	txFinishMsgNotifyFunc func(signal *protogo.DockerVMMessage))) {
	c.putTxReqMsgFunc = txRequestPutFunc
}

func (c *ContractEngineClient) PutFinishMsg(msg *protogo.DockerVMMessage) {
	c.logger.Debugf("[%s] put finish signal to engine signal chan, msgType [%s], chan len: [%d]", msg.TxId,
		msg.Type, len(c.txFinishMsgChan))
	c.txFinishMsgChan <- msg
}

// sendTxFinishMsg send message to contract engine
func (c *ContractEngineClient) sendTxFinishMsg() error {
	for msg := range c.txFinishMsgChan {
		c.logger.Debugf("[%s] get signal from engine signal chan", msg.TxId)

		timeByte, _ := time.Now().MarshalBinary()
		msg.Response = &protogo.TxResponse{
			Result: timeByte,
		}

		if err := c.sendMessage(msg); err != nil {
			c.logger.Errorf("send finish message to engine failed, err:%s", err)
			return err
		}
	}

	return nil
}

// sendMessage Send on the gRPC client.
func (c *ContractEngineClient) sendMessage(msg *protogo.DockerVMMessage) error {
	c.serialLock.Lock()
	defer c.serialLock.Unlock()

	c.logger.Debugf("[%s] sending signal to engine, msg: [%+v]", msg.TxId, msg)
	return c.rpcClient.Send(msg)
}
