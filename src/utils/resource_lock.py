from asyncio import Lock

resource_locks = {}


# Функция для получения блокировки по ключу (имя ресурса и его тип)
async def get_resource_lock(namespace: str, resource_name: str, resource_type: str):
    key = f"{resource_type}-{namespace}-{resource_name}"
    if key not in resource_locks:
        resource_locks[key] = Lock()
    return resource_locks[key]
