# MySQL pelo PyMySQL (Python puro, sem compilar mysqlclient no Windows). O Django
# só aceita o backend 'mysql' se o módulo MySQLdb existir; o PyMySQL assume esse nome.
try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:
    pass
