from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

# 连接参数
host = "192.168.31.28"
port = 27017
username = "vnpyUser"
password = "123456"
database_name = "vnpy_position"
auth_database = "admin"          # 用户所在的认证数据库，根据实际情况修改

# 构建连接 URI（如果密码包含特殊字符，需要 URL 编码）
uri = f"mongodb://{username}:{password}@{host}:{port}/{database_name}?authSource={auth_database}"

try:
    # 1. 建立连接
    client = MongoClient(uri)
    # 可选：发送一个命令测试连接是否成功
    client.admin.command('ping')
    print("✅ 连接成功！")

    # 2. 选择数据库
    db = client[database_name]

    # 3. 获取所有集合名称
    collections = db.list_collection_names()
    print(f"📁 数据库 '{database_name}' 中的集合：{collections}")

    if not collections:
        print("该数据库中没有集合。")
    else:
        # 4. 遍历每个集合，获取其索引
        for coll_name in collections:
            print(f"\n🔍 集合：{coll_name}")
            indexes = db[coll_name].index_information()
            if indexes:
                for idx_name, idx_info in indexes.items():
                    print(f"  索引名称：{idx_name}")
                    print(f"  索引字段：{idx_info['key']}")
                    print(f"  是否唯一：{idx_info.get('unique', False)}")
                    print()
            else:
                print("  该集合没有索引（除了默认的 _id_ 索引）")
    client.close()
except ConnectionFailure as e:
    print(f"❌ 连接失败：{e}")
except OperationFailure as e:
    print(f"❌ 操作失败（可能是认证问题）：{e}")
except Exception as e:
    print(f"❌ 发生未知错误：{e}")