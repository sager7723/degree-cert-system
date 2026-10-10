/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sandbox

import (
	"os"
	"os/signal"
	"syscall"
	"time"

	"chainmaker.org/chainmaker/contract-sdk-go/v2/sdk"
)

const (
	version = "Contract_SDK_Version:fc5101a7f55d71e234242163fd1bdfaa4fdea7437bf161f7e1cf7c49e57580a2"

	ExitCode_UNKNOWN = 1
	ExitCode_SIGINT  = 128 + 2
	ExitCode_SIGTERM = 128 + 15
)

func Start(contract sdk.Contract) error {

	if err := initConfig(); err != nil {
		return err
	}

	contractLoggerModule = generateLoggerModuleName(MODULE_CONTRACT, config.ProcessName)

	logger := newDockerLogger(generateLoggerModuleName(MODULE_SANDBOX, config.ProcessName), config.LogLevel)
	logger.Debug(version)

	// engine client
	engineRPCClient, err := newUDSClient()
	if err != nil {
		logger.Errorf("new engine client failed, err: %s", err.Error())
		return err
	}
	engineClient := newContractEngineClient(engineRPCClient, config.ProcessName, config.ContractName, logger)

	runtimeRPCClient, err := newRuntimeConn()
	if err != nil {
		logger.Errorf("new runtime client failed, err: %s", err.Error())
		return err
	}
	runtimeClient := newRuntimeClient(runtimeRPCClient, config.ContractName, logger)

	txHandler := newTxHandler(contract, config.ProcessName, config.ContractName, config.ContractAddr, logger)

	// Register
	txHandler.RegisterSyscallMsgSendFunc(runtimeClient.PutMsgWithNotify)
	engineClient.RegisterTxRequestPutFunc(txHandler.PutMsgWithNotify)

	errCh := make(chan error, 1)
	go func() {
		errCh <- txHandler.Start()
	}()

	go func() {
		errCh <- runtimeClient.Start()
	}()

	go func() {
		errCh <- engineClient.Start()
	}()

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)

	logger.Infof("runtime client started, contract engine client started, tx handler started, all is well!")

	for {
		select {
		case err = <-errCh:
			logger.Errorf("sandbox stop with error, err: %s", err.Error())
			return err

		case sig := <-sigs:
			switch sig {
			case syscall.SIGTERM:
				logger.Debugf("receive signal [%d:%s], exit normally", sig, sig.String())
				logger.Debug("sandbox - end")
				err := runtimeClient.rpcClient.CloseSend()
				if err != nil {
					logger.Errorf("close runtime client send failed, err: %s", err.Error())
				}
				go func() {
					waitTime := 2 * time.Second
					time.Sleep(waitTime)
					logger.Warnf("sandbox - force exit after sleep [%s:%s]", sig.String(), waitTime)
					os.Exit(ExitCode_SIGTERM)
				}()
			case syscall.SIGINT:
				// print tx duration and stack
				logger.Infof("receive signal [%d:%s]", sig, sig.String())
				logger.Infof("syscall statistics of current tx %s, start time: %v, end time: %v, current status: %v, syscalls: %s, steps: %s",
					currentTxDuration.Tx.TxId, time.Unix(0, currentTxDuration.StartTime), time.Unix(0, currentTxDuration.EndTime),
					currentStatus, currentTxDuration.PrintSysCallList(), PrintTxSteps(currentTxDuration.Tx))
				logger.Infof("stack info: %s", GetAllStackMsg())
				logger.Debug("sandbox - end")
				os.Exit(ExitCode_SIGINT)
			default:
				logger.Infof("receive signal [%d:%s] from unknown source", sig, sig.String())

			}
		}
	}

	logger.Debug("sandbox - end")
	return err
}
