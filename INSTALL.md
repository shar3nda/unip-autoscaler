## Необходимые условия

- Кластер Kubernetes версии 1.26 или выше
- Конфигурационный файл для kubectl, настроенный на работу с кластером
- Доступ к реестру образов Docker
- Развернутый и настроенный сервер метрик (Prometheus или совместимый)

## Установка

Перед началом установки необходимо собрать Docker-образ автоскейлера и загрузить его в реестр Docker.

Для включения автогибернации сервисов необходимо настроить `alertmanager` для отправки
уведомлений о неиспользуемом сервисе (например, кол-во запросов на сервис равно 0 в течение 1 часа).

```http
POST /alert HTTP/1.1
Host: <your-autoscaler-host>
Content-Type: application/json

{"commonAnnotations": {"namespace": "pu-test-pa-test", "service": "test-app-service"}}
```

Необходимо также прописать в deployment корректный тег образа, загруженный в реестр Docker, и указать
в Ingress нужный хост (см. файл [k8s/deployment.yaml](k8s/deployment.yaml)).

Настройки автоскейлера находятся в ConfigMap `autoscaler-config` и `autoscaler-props` в пространстве имен `unip-system-autoscaler`.
Больше информации о конфигурации в [README.md](README.md).

## Порядок применения манифестов

1. `ns.yaml` - пространство имен `unip-system-autoscaler`
2. `service-account.yaml` - сервисный аккаунт для автоскейлера
3. `registry-credentials-secret.yaml` - секрет с кредами для реестра Docker
4. `cm.yaml` - глобальная конфигурация
5. `autoscaler-props.yaml` - конфигурация для приложений
6. `deployment.yaml` - деплоймент автоскейлера

Для проверки работы автоскейлера можно использовать stress-ng (деплоймент в [k8s/stress-ng.yaml](k8s/stress-ng.yaml)),
который создает нагрузку на CPU и память.

Также, можно использовать тестовый MLComponent [mlops_platform/demo-project](https://platform.stratpro.hse.ru/forgejo/mlops_platform/demo-project/)
