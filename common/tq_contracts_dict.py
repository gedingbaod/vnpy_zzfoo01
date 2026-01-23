import pandas as pd
from tqsdk import TqApi
from typing import Dict

from common.main_contracts import futures_main_contracts


class TqContractsDict:

    def __init__(self, tq_api: TqApi):
        self.api = tq_api
        if self.api is None:
            raise "api 不能为空"
        self.contracts: Dict[str, object] = {}  # 合约行情对象字典

    def get_contracts(self):
        if self.contracts == {}:
            self.get_all_products()
        return self.contracts

    def get_all_products(self):
        for exchange, products in futures_main_contracts.items():
            print(f"\n处理交易所: {exchange}")
            for product_code, product_name in products.items():
                print(f"\n处理商品: {product_code} {product_name}")
                dict_p = self.get_all_contracts_for_product(exchange, product_code)
                self.contracts.update(dict_p)

    def get_all_contracts_for_product(self, exchange, product_code):
        """
        获取指定品种的所有可交易合约
        """
        try:
            # 先变小写
            if exchange != "CFFEX" and exchange != "CZCE":
                product_code = product_code.lower()  #"DEC" "INE" "SHFE" "GFEX"
            # 获取所有合约
            all_contracts = self.api.query_quotes(ins_class=["FUTURE"], product_id=product_code, expired=False)
            # https://doc.shinnytech.com/tqsdk/latest/reference/tqsdk.api.html#tqsdk.TqApi.query_symbol_info
            df = self.api.query_symbol_info(sorted(all_contracts))
            # 处理时间戳，转换为东八区时间
            df['expire_datetime'] = pd.to_datetime(df['expire_datetime'], unit='s')
            # 设置为UTC时区，然后转换为东八区
            df['expire_datetime'] = df['expire_datetime'].dt.tz_localize('UTC').dt.tz_convert('Asia/Shanghai')

            # 过滤出指定交易所和品种的合约
            # product_contracts = []
            product_contracts: Dict[str, dict] = {}
            for index, contract in df.iterrows():
                # 检查交易所和品种
                if contract["instrument_id"].startswith(f"{exchange}.{product_code}"):
                    # 获取合约的详细信息
                    try:

                        # 检查是否可交易（有最新价和持仓量）
                        if hasattr(contract, 'pre_close') and hasattr(contract, 'pre_open_interest'):
                            product_contracts[contract["instrument_id"]] = {
                                'instrument_id': contract.get('instrument_id'),
                                'instrument_name': contract.get('instrument_name'),
                                'pre_settlement': contract.get('pre_settlement'),
                                'pre_open_interest': contract.get('pre_open_interest'),
                                'pre_close': contract.get('pre_close'),
                                'expire_datetime': contract.get('expire_datetime')
                            }
                    except:
                        continue

            return product_contracts

        except Exception as e:
            print(f"获取{exchange}.{product_code}合约失败: {e}")
            return []

if __name__ == "__main__":
    from account.tq_account import tq_auth
    api = TqApi(auth=tq_auth)
    print("实时模式连接成功")

    contracts = TqContractsDict(api)

    print(contracts.get_contracts())
    print('=' * 60)
    print(contracts.get_contracts())

    api.close()
