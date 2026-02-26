from pymongo import MongoClient

client = MongoClient("mongodb://rootAdmin:123456@192.168.31.28:27017/?authSource=admin")

# 删除 admin 数据库中的 vnpyUser
admin_db = client.admin
admin_db.command("dropUser", "vnpyUser")
print("已删除 admin 数据库中的 vnpyUser")

# 删除 vnpy_position 数据库中的 vnpyUser
position_db = client.vnpy_position
position_db.command("dropUser", "vnpyUser")
print("已删除 vnpy_position 数据库中的 vnpyUser")

client.close()