import pymssql

conn = pymssql.connect(server="192.168.5.10", user="mi", password="miadmin", port=1433)
cur = conn.cursor()

cur.execute("""
    SELECT TOP 3
        user_name,
        LEN(user_password) AS pwd_length,
        LEFT(user_password, 6) AS pwd_prefix,
        actived
    FROM [MI_AUTHEN].dbo.tb_user_account
    WHERE actived = 1
""")

for row in cur.fetchall():
    print(f"user: {row[0]} | pwd_length: {row[1]} | prefix: {row[2]} | active: {row[3]}")

conn.close()
