/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sandbox

import (
	"errors"

	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
	"go.uber.org/zap"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type RuntimeClient struct {
	logger         *zap.SugaredLogger
	rpcClient      protogo.DockerVMRpc_DockerVMCommunicateClient
	contractName   string
	txId           string
	responseNotify func(msg *protogo.DockerVMMessage)
	sendMsgCh      chan *protogo.DockerVMMessage
	stopReceive    chan struct{}
	stopSend       chan struct{}
}

func newRuntimeClient(rpcClient protogo.DockerVMRpc_DockerVMCommunicateClient, contractName string, logger *zap.SugaredLogger) *RuntimeClient {
	return &RuntimeClient{
		logger:       logger,
		rpcClient:    rpcClient,
		contractName: contractName,
		sendMsgCh:    make(chan *protogo.DockerVMMessage, 1),
		stopSend:     make(chan struct{}, 1),
		stopReceive:  make(chan struct{}, 1),
	}
}

func (r *RuntimeClient) Start() error {
	r.logger.Debugf("*** runtime client started ***")
	defer func() {
		err := r.rpcClient.CloseSend()
		if err != nil {
			return
		}
	}()

	errCh := make(chan error, 2)

	go func() {
		if err := r.recvRoutine(); err != nil {
			errCh <- err
		}
	}()

	go func() {
		if err := r.sendRoutine(); err != nil {
			errCh <- err
		}
	}()

	return <-errCh
}

func (r *RuntimeClient) recvRoutine() error {
	for {
		select {
		case <-r.stopReceive:
			r.logger.Debugf("close runtime client receive goroutine")
			return errors.New("close runtime client receive goroutine")
		default:
			receivedMsg, recvErr := r.rpcClient.Recv()

			if recvErr != nil {
				r.logger.Infof("client receive err from runtime %s, process exit", recvErr)
				close(r.stopSend)
				return recvErr
			}

			r.logger.Debugf("[%s] receive msg from runtime server, msg type [%s]", receivedMsg.TxId, receivedMsg.Type)

			switch receivedMsg.Type {
			case protogo.DockerVMType_GET_STATE_RESPONSE,
				protogo.DockerVMType_GET_BATCH_STATE_RESPONSE,
				protogo.DockerVMType_CREATE_KV_ITERATOR_RESPONSE,
				protogo.DockerVMType_CONSUME_KV_ITERATOR_RESPONSE,
				protogo.DockerVMType_CREATE_KEY_HISTORY_TER_RESPONSE,
				protogo.DockerVMType_CONSUME_KEY_HISTORY_ITER_RESPONSE,
				protogo.DockerVMType_GET_SENDER_ADDRESS_RESPONSE,
				protogo.DockerVMType_CALL_CONTRACT_RESPONSE:
				if r.responseNotify == nil {
					r.logger.Errorf("[%s] failed to handle resposne, sys_call responseNotify is nil", receivedMsg.TxId)
					continue
				}
				if err := currentTxDuration.EndSysCall(receivedMsg); err != nil {
					r.logger.Warnf("failed to end syscall, %v", err)
				}
				r.responseNotify(receivedMsg)

			default:
				r.logger.Errorf("unknown message type, received msg: [%v]", receivedMsg)
			}
		}
	}
}

func (r *RuntimeClient) sendRoutine() error {
	r.logger.Debugf("start sending sys_call message")
	for {
		select {
		case <-r.stopSend:
			r.logger.Debugf("close runtime client send goroutine")
			return errors.New("close runtime client send goroutine")

		case msg := <-r.sendMsgCh:
			if msg.Type != protogo.DockerVMType_TX_RESPONSE {
				currentTxDuration.StartSysCall(msg)
			}

			r.logger.Debugf("[%s] get msg from runtime response chan, msgType: [%s]", msg.TxId, msg.Type)
			//EnterNextStep(msg, protogo.StepType_SANDBOX_GRPC_SEND_CHAIN_RESP)
			if err := r.sendMessage(msg); err != nil {
				r.logger.Errorf("send msg to runtime failed, err:%s", err)
				errStatus, _ := status.FromError(err)
				if errStatus.Code() != codes.ResourceExhausted {
					close(r.stopReceive)
					return err
				}
			}
		}
	}

}

// PutMsgWithNotify put msg nad register call back func for sys_call response
func (r *RuntimeClient) PutMsgWithNotify(msg *protogo.DockerVMMessage,
	syscallResponseNotifyFunc func(msg *protogo.DockerVMMessage)) {
	r.RegisterResponseNotifier(syscallResponseNotifyFunc)
	r.PutMsg(msg)
}

// PutMsg put msg to send channel
func (r *RuntimeClient) PutMsg(msg *protogo.DockerVMMessage) {
	r.logger.Debugf("[%s] put msg to runtime response chan, chan len: [%d]", msg.TxId, len(r.sendMsgCh))

	r.sendMsgCh <- msg
}

// RegisterResponseNotifier register callback func for sys_call response
func (r *RuntimeClient) RegisterResponseNotifier(syscallResponseNotifyFunc func(msg *protogo.DockerVMMessage)) {
	r.responseNotify = syscallResponseNotifyFunc
}

func (r *RuntimeClient) sendMessage(msg *protogo.DockerVMMessage) error {
	r.logger.Debugf("[%s] sending response to runtime server, type: %s", msg.TxId, msg.Type)
	return r.rpcClient.Send(msg)
}
