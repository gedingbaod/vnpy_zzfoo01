from pymongo import MongoClient, ASCENDING
from pymongo.errors import OperationFailure

# ================== 配置信息 ==================
ADMIN_URI = "mongodb://rootAdmin:123456@192.168.31.28:27017/?authSource=admin"
NEW_USER = "vnpyUser"
NEW_PWD = "123456"
TARGET_DB = "vnpy_position"
COLLECTIONS = ["spread_position_history", "test"]   # 需要确保存在的集合

# ================== 1. 以管理员身份连接 ==================
admin_client = MongoClient(ADMIN_URI)
admin_db = admin_client.admin

# ================== 2. 检查并创建数据库（如果需要）==================
# 获取当前所有数据库列表
existing_dbs = admin_client.list_database_names()
print(f"当前数据库列表: {existing_dbs}")

if TARGET_DB not in existing_dbs:
    print(f"数据库 {TARGET_DB} 不存在，正在创建...")
    # 通过创建一个集合来实际创建数据库
    target_db = admin_client[TARGET_DB]
    target_db.create_collection("_dummy")   # 创建临时集合触发数据库创建
    # 删除临时集合（可选）
    target_db.drop_collection("_dummy")
    print(f"✅ 数据库 {TARGET_DB} 已创建")
else:
    print(f"数据库 {TARGET_DB} 已存在")
    target_db = admin_client[TARGET_DB]

# ================== 3. 检查并创建需要的集合 ==================
existing_colls = target_db.list_collection_names()
print(f"数据库 {TARGET_DB} 中现有集合: {existing_colls}")

for coll in COLLECTIONS:
    if coll not in existing_colls:
        target_db.create_collection(coll)
        print(f"📁 集合 {coll} 创建成功")
    else:
        print(f"ℹ️ 集合 {coll} 已存在，跳过创建")

# ================== 4. 检查并创建/更新用户 ==================
def check_user(check_db):
    # 检查用户是否存在（需要切换到用户所在的认证数据库，这里是 admin）
    user_info = None
    try:
        user_info = check_db.command("usersInfo", NEW_USER)
    except OperationFailure as e:
        print(f"查询用户信息失败: {e}")

    if user_info and user_info.get("users"):
        print(f"用户 {NEW_USER} 已存在，正在更新角色和密码...")
        check_db.command("updateUser", NEW_USER, pwd=NEW_PWD, roles=[
            {"role": "readWrite", "db": TARGET_DB},
            {"role": "dbAdmin", "db": TARGET_DB}
        ])
        print(f"🔄 用户 {NEW_USER} 更新成功")
    else:
        print(f"用户 {NEW_USER} 不存在，正在创建...")
        check_db.command("createUser", NEW_USER, pwd=NEW_PWD, roles=[
            {"role": "readWrite", "db": TARGET_DB},
            {"role": "dbAdmin", "db": TARGET_DB}
        ])
        print(f"✅ 用户 {NEW_USER} 创建成功")

# 用于python代码连接
check_user(admin_db)
# 用于navicat连接
check_user(target_db)

# ================== 5. 验证用户权限 ==================
print("\n=== 使用新用户测试权限 ===")
user_uri = f"mongodb://{NEW_USER}:{NEW_PWD}@192.168.31.28:27017/{TARGET_DB}?authSource=admin"
user_client = MongoClient(user_uri)
test_db = user_client[TARGET_DB]

def check_coll_test():
    # 尝试插入数据
    test_db.test.insert_one({"symbol": "TEST", "qty": 100})
    print("✅ 插入数据成功")

    # 查询数据
    doc = test_db.test.find_one({"symbol": "TEST"})
    print(f"查询结果: {doc}")

    # 创建索引
    test_db.test.create_index("qty", name="qty_1")
    print("✅ 创建索引成功")

    # 删除索引（需要 dbAdmin 角色）
    test_db.test.drop_index("qty_1")
    print("✅ 删除索引成功")

    # 清理测试数据
    test_db.test.delete_many({"symbol": "TEST"})
    print("🧹 测试数据已清理")

check_coll_test()

def check_coll_spread():
    # 创建集合（表）
    collection_name = "spread_position_history"

    # 检查集合是否已存在
    existing_collections = test_db.list_collection_names()
    if collection_name in existing_collections:
        print(f"集合 '{collection_name}' 已存在")
        collection = test_db[collection_name]
    else:
        # 明确创建集合
        collection = test_db.create_collection(collection_name)
        print(f"集合 '{collection_name}' 创建成功")

    # 创建唯一索引（用于去重）
    # 组合键：near_symbol + far_symbol + open_start_time
    index_name = "unique_position_key"
    try:
        collection.create_index(
            [
                ("near_symbol", ASCENDING),
                ("far_symbol", ASCENDING),
                ("open_start_time", ASCENDING),
            ],
            unique=True,
            name=index_name
        )
        print(f"唯一索引 '{index_name}' 创建成功")
        print(f"索引字段: near_symbol, far_symbol, open_start_time")
    except Exception as e:
        print(f"创建索引失败（可能已存在）: {str(e)}")

    # 显示当前所有索引
    print("\n当前集合的所有索引:")
    indexes = collection.list_indexes()
    for idx in indexes:
        print(f"  - {idx['name']}: {idx['key']}")

    # 测试插入一条数据（可选）
    print("\n是否测试插入数据？(y/n): ", end="")
    # choice = input().strip().lower()
    choice = 'y'
    if choice == 'y':
        test_document = {
            "near_symbol": "sn2603",
            "far_symbol": "sn2604",
            "open_start_time": "2026-01-01 10:00:00",
            "position_status": "测试数据",
            "test": True
        }

        try:
            result = collection.insert_one(test_document)
            print(f"测试数据插入成功，_id: {result.inserted_id}")

            # 查询测试数据
            found = collection.find_one({"_id": result.inserted_id})
            print(f"查询结果: {found}")

            # 删除测试数据
            collection.delete_one({"_id": result.inserted_id})
            print("测试数据已删除")

        except Exception as e:
            print(f"测试插入失败: {str(e)}")
check_coll_spread()

# 关闭连接
admin_client.close()
user_client.close()

print("\n🎉 所有操作完成！")