# GCP IAM Permission Tables

## gcloud compute

| Command | Permissions Required |
|---------|---------------------|
| `instance-templates list` | `compute.instanceTemplates.list` |
| `instance-templates describe` | `compute.instanceTemplates.get` |
| `instance-templates set on MIG` | `compute.instanceTemplates.useReadOnly` |
| `instance-groups managed describe` | `compute.instanceGroupManagers.get` |
| `instance-groups managed list` | `compute.instanceGroupManagers.list` |
| `instance-groups managed set-autoscaling` | `compute.autoscalers.update` + `compute.autoscalers.create` |
| `instance-groups managed set-instance-template` | `compute.instanceGroupManagers.update` + `compute.instanceTemplates.useReadOnly` + `compute.instances.create` + `compute.instances.setMetadata` + `compute.instances.setTags` + `compute.instances.setLabels` + `compute.disks.create` + `compute.subnetworks.use` |
| `instance-groups managed rolling-action start-update` | `compute.instanceGroupManagers.update` + `compute.instanceTemplates.useReadOnly` + `compute.instances.delete` |
| `instance-groups managed wait-until` | `compute.instanceGroupManagers.get` |
| `instance-groups managed list-instances` | `compute.instanceGroupManagers.list` |
| `instances describe` | `compute.instances.get` |
| `instances start` | `compute.instances.start` |
| `instances stop` | `compute.instances.stop` |
| `instances create` | `compute.instances.create` + `compute.disks.create` + `compute.subnetworks.use` |
| `instances delete` | `compute.instances.delete` |
| `instances setMetadata` | `compute.instances.setMetadata` |
| `instances list` | `compute.instances.list` |
| `disks create` | `compute.disks.create` |
| `snapshots create` | `compute.snapshots.create` + `compute.disks.createSnapshot` |
| `images create` | `compute.images.create` |
| `firewall-rules create/update` | `compute.firewalls.create` / `compute.firewalls.update` |
| `networks list/describe` | `compute.networks.list` / `compute.networks.get` |
| `subnetworks list/describe` | `compute.subnetworks.list` / `compute.subnetworks.get` |
| `zones list` | `compute.zones.list` |
| `regions list` | `compute.regions.list` |
| `health-checks describe` | `compute.healthChecks.get` |
| `backend-services update` | `compute.backendServices.update` |

## gcloud sql

| Command | Permissions Required |
|---------|---------------------|
| `instances list` | `cloudsql.instances.list` |
| `instances describe` | `cloudsql.instances.get` |
| `instances patch` | `cloudsql.instances.update` |
| `instances create` | `cloudsql.instances.create` |
| `instances delete` | `cloudsql.instances.delete` |
| `instances export` | `cloudsql.instances.export` |
| `instances import` | `cloudsql.instances.import` |
| `databases create/delete` | `cloudsql.databases.create` / `cloudsql.databases.delete` |
| `users create/delete` | `cloudsql.users.create` / `cloudsql.users.delete` |
| `connect` | `cloudsql.instances.connect` |

## gsutil / gcloud storage

| Command | Permissions Required |
|---------|---------------------|
| `gsutil ls gs://bucket/` | `storage.objects.list` |
| `gsutil ls -b gs://bucket` | `storage.buckets.get` |
| `gsutil stat gs://bucket/object` | `storage.objects.get` |
| `gsutil cp gs://... file` (download) | `storage.objects.get` |
| `gsutil cp file gs://...` (upload) | `storage.objects.create` |
| `gsutil rm gs://bucket/object` | `storage.objects.delete` |
| `gsutil -h "x-goog-if-generation-match:0" cp` | `storage.objects.create` (atomic/conditional) |
| `gsutil mb` | `storage.buckets.create` |
| `gsutil rb` | `storage.buckets.delete` |
| `gsutil iam` | `storage.buckets.getIamPolicy` / `storage.buckets.setIamPolicy` |

## gcloud logging / monitoring

| Command | Permissions Required |
|---------|---------------------|
| Write logs | `logging.logEntries.create` |
| Read logs | `logging.logEntries.list` |
| Write metrics | `monitoring.metricDescriptors.create` + `monitoring.timeSeries.create` |

## gcloud iam

| Command | Permissions Required |
|---------|---------------------|
| `service-accounts get-iam-policy` | `iam.serviceAccounts.getIamPolicy` |
| `service-accounts set-iam-policy` | `iam.serviceAccounts.setIamPolicy` |
| Use a SA as instance identity | `iam.serviceAccounts.actAs` |
| `roles describe` | `iam.roles.get` |

## Predefined Role Shortcuts

| Predefined Role | Key Permissions Included |
|----------------|--------------------------|
| `roles/compute.admin` | All compute.* |
| `roles/compute.instanceAdmin.v1` | instances.*, disks.*, instanceTemplates.get/list — NOT instanceTemplates.useReadOnly |
| `roles/compute.viewer` | compute.*.get + list (read-only) |
| `roles/cloudsql.admin` | All cloudsql.* |
| `roles/cloudsql.client` | cloudsql.instances.connect |
| `roles/cloudsql.editor` | cloudsql.instances.update + connect, NOT create/delete |
| `roles/storage.admin` | All storage.* |
| `roles/storage.objectAdmin` | storage.objects.* (NOT buckets.*) |
| `roles/storage.objectViewer` | storage.objects.get + list |
| `roles/logging.logWriter` | logging.logEntries.create |
| `roles/logging.viewer` | logging.logEntries.list |
| `roles/iam.serviceAccountUser` | iam.serviceAccounts.actAs + getAccessToken |
