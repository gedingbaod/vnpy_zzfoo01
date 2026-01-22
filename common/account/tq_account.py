from tqsdk import TqAuth
from tqsdk import TqAccount


tq_auth=TqAuth("gedingbaod", "3028023abc")

huishang_account = TqAccount("H徽商期货", "232996", "3028023abc")

hongyuan_account = TqAccount("H宏源期货", "902701661", "3028023abc")

__all__ = ['tq_auth', 'huishang_account', 'hongyuan_account']



