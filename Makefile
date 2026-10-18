CLUSTER   ?= bhttp
NAMESPACE ?= bhttp
RELEASE   ?= bserve
IMAGE     ?= ghcr.io/krishna-p060/network-project
# The image tag is the git commit; uncommitted changes are marked so they are never mistaken for it.
VERSION   ?= $(shell git rev-parse --short=7 HEAD)$(shell git diff --quiet HEAD -- . || echo -dirty)

.PHONY: test image cluster deploy smoke status rollback monitoring grafana prometheus clean

test:
	python3 -m unittest discover tests

image:
	docker build --target test -t $(IMAGE):test .
	docker build --target runtime --build-arg VERSION=$(VERSION) -t $(IMAGE):$(VERSION) .

cluster:
	kind get clusters | grep -qx $(CLUSTER) || kind create cluster --config deploy/kind-config.yaml
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	kubectl label namespace $(NAMESPACE) pod-security.kubernetes.io/enforce=restricted --overwrite

# Uses the CI-built image from GHCR when it exists, otherwise builds it locally.
deploy:
	docker pull $(IMAGE):$(VERSION) 2>/dev/null || $(MAKE) image VERSION=$(VERSION)
	kind load docker-image $(IMAGE):$(VERSION) --name $(CLUSTER)
	helm upgrade --install $(RELEASE) charts/bserve -n $(NAMESPACE) \
		-f charts/bserve/values-kind.yaml --set image.tag=$(VERSION) \
		$(if $(shell kubectl get crd servicemonitors.monitoring.coreos.com 2>/dev/null),,--set monitoring.serviceMonitor=false --set monitoring.rules=false) \
		--wait --timeout 3m --history-max 10
	kubectl -n $(NAMESPACE) rollout status deployment/$(RELEASE)

smoke:
	./bcurl -v --request-id manual localhost:9000/hello.txt
	kubectl -n $(NAMESPACE) logs -l app.kubernetes.io/component=server --prefix --tail=50 | grep '"request_id": "manual-1"'

status:
	helm -n $(NAMESPACE) history $(RELEASE)
	kubectl -n $(NAMESPACE) get deploy,rs,pods,svc,pdb,networkpolicy,cronjob -o wide

rollback:
	helm -n $(NAMESPACE) rollback $(RELEASE) --wait
	kubectl -n $(NAMESPACE) rollout status deployment/$(RELEASE)

monitoring:
	kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1 || \
		kubectl -n monitoring create secret generic grafana-admin \
			--from-literal=admin-user=admin --from-literal=admin-password=$$(openssl rand -hex 16)
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
	helm repo update prometheus-community >/dev/null
	helm upgrade --install monitoring prometheus-community/kube-prometheus-stack -n monitoring \
		-f deploy/monitoring-values.yaml --wait --timeout 10m

grafana:
	@echo "user: admin  password: $$(kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)"
	kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80

prometheus:
	kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090

clean:
	kind delete cluster --name $(CLUSTER)
