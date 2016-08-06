from solc import compile_source

from web3.utils.string import (
    force_text,
)

from populus.utils.transactions import (
    wait_for_transaction_receipt,
    get_contract_address_from_txn,
    get_block_gas_limit,
)

from .registrar import (
    REGISTRAR_SOURCE,
    Address,
)


class Operation(object):
    """
    Base class that all migration operations inherit from.
    """
    def execute(self, **kwargs):
        raise NotImplementedError(
            "The `execute` method must be implemented by each Operation subclass"
        )


class RunPython(Operation):
    """
    A migration operation that runs custom python code for executing operations
    that don't fit within the provided operation canvas.
    """
    def __init__(self, callback):
        self.callback = callback

    def execute(self, **kwargs):
        return self.callback(**kwargs)


class SendTransaction(Operation):
    """
    A migration operation that sends a transaction.
    """
    transaction = None
    timeout = 30

    def __init__(self, transaction, timeout=120):
        self.transaction = transaction
        if timeout is not None:
            self.timeout = timeout

    def execute(self, web3, **kwargs):
        transaction_hash = web3.eth.sendTransaction(self.transaction)
        if self.timeout is not None:
            wait_for_transaction_receipt(
                web3, transaction_hash, timeout=self.timeout,
            )
        return {
            'transaction-hash': transaction_hash,
        }


class DeployContract(Operation):
    contract = None
    transaction = None
    timeout = None
    verify = None

    def __init__(self,
                 contract_name,
                 transaction=None,
                 arguments=None,
                 verify=True,
                 auto_gas=True,
                 timeout=120):
        self.contract_name = contract_name

        if timeout is None and verify:
            raise ValueError(
                "Invalid configuration.  When verifying a contracts deployment, "
                "the timeout value must be set."
            )

        if transaction is None:
            transaction = {}

        if 'data' in transaction or 'to' in transaction:
            raise ValueError(
                "Invalid configuration.  You cannot specify `data` or `to` "
                "values in `DeployContract` transactions."
            )

        if auto_gas and 'gas' in transaction:
            raise ValueError(
                "Invalid configuration.  Cannot use `auto_gas` when specifying "
                "a gas value for a transaction"
            )

        if arguments is None:
            arguments = []

        self.auto_gas = auto_gas
        self.transaction = transaction
        self.arguments = arguments
        self.verify = verify

        if timeout is not None:
            self.timeout = timeout

    def execute(self, web3, compiled_contracts, **kwargs):
        contract_data = compiled_contracts[self.contract_name]
        ContractFactory = web3.eth.contract(
            abi=contract_data['abi'],
            code=contract_data['code'],
            code_runtime=contract_data['code_runtime'],
            source=contract_data['source'],
        )

        deploy_transaction = dict(**self.transaction)

        if self.auto_gas:
            gas_estimate_transaction = dict(**self.transaction)
            deploy_data = ContractFactory.encodeConstructorData(self.arguments)
            gas_estimate_transaction['data'] = deploy_data

            gas_estimate = web3.eth.estimateGas(gas_estimate_transaction)

            gas_limit = get_block_gas_limit(web3)

            if gas_estimate > gas_limit:
                raise ValueError(
                    "Contract does not appear to be delpoyable within the "
                    "current network gas limits"
                )

            deploy_transaction['gas'] = min(gas_limit, gas_estimate + 100000)

        deploy_transaction_hash = ContractFactory.deploy(
            deploy_transaction,
            self.arguments,
        )

        if self.timeout is not None:
            contract_address = get_contract_address_from_txn(
                web3, deploy_transaction_hash, timeout=self.timeout,
            )
            if self.verify:
                code = web3.eth.getCode(contract_address)
                if force_text(code) != force_text(ContractFactory.code_runtime):
                    raise ValueError(
                        "An error occured during deployment of the contract."
                    )
            return {
                'contract-address': contract_address,
                'deploy-transaction-hash': deploy_transaction_hash,
                'canonical-contract-address': Address.defer(
                    key='/'.join(('contract', self.contract_name)),
                    value=contract_address,
                ),
            }

        return {
            'deploy-transaction-hash': deploy_transaction_hash,
        }


class TransactContract(Operation):
    contract_name = None
    method_name = None
    arguments = None
    transaction = None

    timeout = None

    def __init__(self,
                 contract_name,
                 method_name,
                 arguments=None,
                 transaction=None,
                 contract_address=None,  # TODO: this should come from the resolver.
                 auto_gas=True,
                 timeout=120):
        self.contract_address = contract_address
        self.contract_name = contract_name
        self.method_name = method_name
        self.auto_gas = auto_gas

        if arguments is None:
            arguments = []
        self.arguments = arguments

        if transaction is None:
            transaction = {}

        if auto_gas and 'gas' in transaction:
            raise ValueError(
                "Invalid configuration.  Cannot use `auto_gas` when specifying "
                "a gas value for a transaction"
            )

        self.transaction = transaction
        self.auto_gas = True

        if timeout is not None:
            self.timeout = timeout

    def execute(self, web3, compiled_contracts, **kwargs):
        contract_data = compiled_contracts[self.contract_name]
        contract = web3.eth.contract(
            address=self.contract_address,
            abi=contract_data['abi'],
            code=contract_data['code'],
            code_runtime=contract_data['code_runtime'],
            source=contract_data['source'],
        )

        transaction = dict(**self.transaction)

        if self.auto_gas:
            gas_estimate_txn = dict(**self.transaction)
            gas_estimator = contract.estimateGas(gas_estimate_txn)
            gas_estimate = getattr(gas_estimator, self.method_name)(*self.arguments)

            gas_limit = get_block_gas_limit(web3)

            if gas_estimate > gas_limit:
                raise ValueError(
                    "Contract transaction appears to execeed the block gas limit"
                )

            transaction['gas'] = min(gas_limit, gas_estimate + 100000)

        transactor = contract.transact(self.transaction)
        method = getattr(transactor, self.method_name)
        transaction_hash = method(*self.arguments)

        if self.timeout is not None:
            wait_for_transaction_receipt(
                web3, transaction_hash, timeout=self.timeout,
            )

        return {
            'deploy-transaction-hash': transaction_hash,
        }


class DeployRegistrar(DeployContract):
    def __init__(self, **kwargs):
        super(DeployRegistrar, self).__init__(
            contract_name="Registrar",
            **kwargs
        )

    def execute(self, web3, **kwargs):
        kwargs.pop('compiled_contracts', None)
        compiled_contracts = compile_source(REGISTRAR_SOURCE)
        return super(DeployRegistrar, self).execute(
            web3=web3,
            compiled_contracts=compiled_contracts,
            **kwargs
        )