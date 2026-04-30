# DentTime C4 Deployment Diagram — Design Spec

**Date:** 2026-04-30  
**Scope:** Future production target deployment (GKE Autopilot + KServe + Triton)  
**Output:** `c4/denttime_c4_deployment.puml`

---

## Goal

Create a C4 Deployment Diagram that shows where each component of DentTime runs in the future production target architecture — both the ML training pipeline and the model serving infrastructure — using the PlantUML C4-PlantUML library.

## Approach

Environment-first layout (Option A): top-level `Deployment_Node`s mirror real infrastructure zones. Relationships show artifact flow and runtime communication. Scope covers training pipeline (Airflow, MLflow, GCS publish) and serving infrastructure (GKE serving namespace) as a single diagram.

---

## Deployment Node Hierarchy

```
[External]  Clinic Staff Browser
[External]  Clinic Management System (CMS)  — MySQL/PostgreSQL

[GCS]       Google Cloud Storage
              └─ denttime-models/  (versioned ONNX model artifacts)

[GKE Autopilot Cluster]
  ├─ Namespace: denttime-training
  │    ├─ Pod: postgres          (PostgreSQL 15 — Airflow metadata + MLflow backend store)
  │    ├─ Pod: mlflow            (MLflow Tracking Server — experiment registry)
  │    ├─ Pod: airflow-webserver (Airflow Webserver — DAG trigger UI :8080)
  │    └─ Pod: airflow-scheduler (Airflow Scheduler — executes DAGs)
  │
  └─ Namespace: denttime-serving
       ├─ InfraNode: Istio Ingress Gateway   (TLS termination, load balancing)
       ├─ Pod: kserve-traffic-router         (Canary split: Shadow → 10% → 100%)
       ├─ Pod: triton-prod                   (Triton + ONNX Runtime — production)
       ├─ Pod: triton-shadow                 (Triton — shadow mode, silent evaluation)
       ├─ Pod: eval-logger                   (Prometheus — compares prod vs shadow predictions)
       ├─ Pod: grafana                       (Grafana — monitoring dashboards)
       └─ InfraNode: KPA/HPA Auto-Scaler     (scales triton-prod replicas on RPS/CPU)
```

---

## Relationships

### Training pipeline (offline / batch)
| From | To | Label | Protocol |
|---|---|---|---|
| CMS | airflow-scheduler | Raw appointment records | SQL / CSV Export |
| airflow-scheduler | mlflow | Log experiments & metrics | MLflow API |
| airflow-scheduler | postgres | Airflow metadata | SQL |
| mlflow | postgres | MLflow backend store | SQL |
| mlflow | GCS | Publish approved ONNX model | GCS API |

### Serving pipeline (online / on-demand)
| From | To | Label | Protocol |
|---|---|---|---|
| Clinic Staff Browser | Istio Ingress Gateway | Inference request | HTTPS |
| Istio | kserve-traffic-router | Forward request | gRPC |
| Istio | triton-shadow | Mirror request (silent) | gRPC |
| kserve-traffic-router | triton-prod | Route production traffic | gRPC |
| triton-prod | Clinic Staff Browser | Duration slot response | JSON / gRPC |
| GCS | triton-prod | Hot-reload ONNX model | GCS API |
| GCS | triton-shadow | Hot-reload ONNX model | GCS API |

### Monitoring & feedback loop
| From | To | Label | Protocol |
|---|---|---|---|
| triton-prod | eval-logger | Production predictions & metrics | Prometheus |
| triton-shadow | eval-logger | Shadow predictions & metrics | Prometheus |
| eval-logger | kserve-traffic-router | Promote / rollback signal | Internal API |
| triton-prod | KPA/HPA Auto-Scaler | Resource utilization metrics | K8s Metrics API |
| KPA/HPA Auto-Scaler | GKE Autopilot | Request additional compute nodes | GKE API |
| grafana | eval-logger | Queries metrics | Prometheus HTTP |

---

## PlantUML Implementation Notes

- Include: `C4_Deployment.puml` from plantuml-stdlib C4-PlantUML
- Top-level nodes: `Deployment_Node` for GKE cluster, GCS, browser, CMS
- Nested nodes: `Deployment_Node` for namespaces, pods
- Infrastructure items: `InfrastructureNode` for Istio gateway and KPA/HPA
- Running containers: `Container_Instance` for Airflow, MLflow, Triton, etc.
- Use `LAYOUT_WITH_LEGEND()` consistent with existing diagrams
- Title: `[Deployment Diagram] DentTime — Future Production Target (GKE Autopilot)`
