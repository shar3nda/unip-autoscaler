# unip-system-autoscaler

В репозитории находится исходный код модуля автомасштабирования (автоскейлера) для платформы MLOps.

Он предназначен для автоматического управления количеством реплик приложений в Kubernetes
на основе метрик, собираемых сервером метрик (например, Prometheus).

Модуль поддерживает гибернацию и автовосстановление сервисов при отсутствии запросов в
течение определенного времени.

## Принцип работы

Состояния приложений, которыми управляет автоскейлер, задаются с помощью количества реплик
и условий перехода к другим возможным состояниям. Условиями являются значения метрик, собираемых
сервером метрик, и их пороговые значения.

Автоскейлер периодически запрашивает метрики у сервера метрик и сравнивает их с пороговыми значениями,
заданными в конфигурации.
Если условие выполнено, количество реплик изменяется.

Если количество запросов к сервису равно 0 в течение определенного времени, автоскейлер
устанавливает количество реплик в 0 и переводит сервис в режим гибернации.
Если на гибернированный сервис приходит запрос, автоскейлер ждёт восстановления сервиса,
возвращая 307 Temporary Redirect (количество перенаправлений и таймаут запроса считаются
автоматически на основе User-Agent).

## Конфигурация

### Глобальная конфигурация

Используется ConfigMap `autoscaler-config` в пространстве имен `unip-system-autsocaler`.
Пример конфигурации:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: autoscaler-config
  namespace: unip-system-autoscaler
data:
  AUTOSCALER_READINESS_PROBE_TIMEOUT: "15" # Таймаут HTTP-запроса для проверки готовности
  AUTOSCALER_READINESS_LIMIT: "30" # Лимит запросов для проверки готовности
  AUTOSCALER_READINESS_TIMEOUT: "1" # Кулдаун после неудачной проверки готовности
  AUTOSCALER_CHECK_INTERVAL: "2" # Интервал запроса метрик
  PROMETHEUS_URL: "http://prometheus-operated.unip-system-prometheus.svc.cluster.local:9090/api/v1/query" # URL API сервера метрик
  DEBUG: "true" # Включение отладки (больше информации в логах)
  AUTOSCALER_NAMESPACE_REGEX: "^pu-[a-zA-Z-]+$" # Регулярное выражение для допустимых пространств имен
```

### Конфигурация для приложений

Конфигурация масштабирования выражается с помощью объекта ScalingConfig.

Его схема находится в `k8s/crds.yaml`.

```yaml
apiVersion: autoscaler.unified-platform.cs.hse.ru/v1alpha1
kind: ScalingConfig
metadata:
  name: my-app-autoscaler
  namespace: unip-system-autoscaler
spec:
  target:
    kind: deployment # Тип объекта, deployment или service
    name: tesseract-app-deployment # Имя объекта
    namespace: pu-test-tesseract # Пространство имен
  states:
    # Каждое состояние содержит количество реплик и, опционально, условия перехода
    # к следующему состоянию.
    - replicas: 1
      transitions:
        - nextState: 2
          conditions:
            # Условия перехода между состояниями, где allConditions - логическое И,
            # anyCondition - логическое ИЛИ (можно использовать только одно).
            allConditions:
              # Условие состояит из имени метрики, оператора ("gt" или "lt") и порогового
              # значения (вещественное число).
              - metric: mycpu
                operator: "gt"
                value: 20.0
              - metric: queries
                operator: "gt"
                value: 200
    - replicas: 2
      transitions:
        - nextState: 3
          conditions:
            allConditions:
              - metric: queries
                operator: "gt"
                value: 500
        - nextState: 1
          conditions:
            anyCondition:
              - metric: mycpu
                operator: "lt"
                value: 10.0
              - metric: queries
                operator: "lt"
                value: 200
    - replicas: 3
      # У состояния может не быть переходов; тогда выход будет возможен только
      # через автоматическую гибернацию.
  prometheusMetrics:
    # Метрики для мониторинга. Каждая метрика содержит имя и запрос PromQL.
    # В запросе можно использовать переменные {{ DEPLOYMENT_NAME }}, {{ SERVICE_NAME }} и {{ NAMESPACE }}.
    # Имеются встроенные метрики с названиями cpu и memory; здесь их указывать не нужно.
    - name: mycpu
      query: >-
        sum by (pod) (rate(container_cpu_usage_seconds_total{pod=~"{{ DEPLOYMENT_NAME }}-.*"}[5m])) * 100
    - name: mymemory
      query: >-
        sum(container_memory_usage_bytes{pod=~"{{ DEPLOYMENT_NAME }}-.*"}) / 1024 / 1024
    - name: queries
      query: >-
        sum(rate(nginx_ingress_controller_requests{ingress="service-api-ingress", namespace="{{ NAMESPACE }}"}[10m]))
  scalingOptions:
    # cpuTimeWindow: 300 # Время окна для усреднения метрики cpu
    # memoryTimeWindow: 300 # -//- memory
    cooldown: 300 # Время ожидания перед следующим изменением количества реплик
    hibernationEnabled: false # Включение автоматической гибернации
  ```