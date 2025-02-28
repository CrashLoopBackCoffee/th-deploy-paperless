import deploy_base
import deploy_base.opnsense
import deploy_base.opnsense.unbound
import deploy_base.opnsense.unbound.host_override
import pulumi as p
import pulumi_kubernetes as k8s
import pulumi_random as random

from paperless.config import ComponentConfig

REDIS_PORT = 6379
PAPERLESS_PORT = 8000


class Paperless(p.ComponentResource):
    def __init__(
        self,
        component_config: ComponentConfig,
        k8s_provider: k8s.Provider,
    ):
        super().__init__('paperless', 'paperless')

        admin_username = 'admin'
        admin_password = random.RandomPassword('admin-password', length=32, special=False).result
        p.export('admin_username', admin_username)
        p.export('admin_password', admin_password)

        namespace = k8s.core.v1.Namespace(
            'paperless-namespace',
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name='paperless',
            ),
            opts=p.ResourceOptions(
                parent=self,
                provider=k8s_provider,
            ),
        )

        namespaced_provider = k8s.Provider(
            'paperless-provider',
            kubeconfig=k8s_provider.kubeconfig,  # type: ignore
            namespace=namespace.metadata['name'],
        )

        k8s_opts = p.ResourceOptions(
            parent=self,
            provider=namespaced_provider,
        )

        app_labels_redis = {'app': 'redis'}
        redis_sts = k8s.apps.v1.StatefulSet(
            'redis',
            metadata={'name': 'redis'},
            spec={
                'replicas': 1,
                'selector': {'match_labels': app_labels_redis},
                'service_name': 'redis-headless',
                'template': {
                    'metadata': {'labels': app_labels_redis},
                    'spec': {
                        'containers': [
                            {
                                'name': 'redis',
                                'image': f'docker.io/library/redis:{component_config.redis.version}',
                                'ports': [{'container_port': REDIS_PORT}],
                            },
                        ],
                    },
                },
            },
            opts=k8s_opts,
        )
        redis_service = k8s.core.v1.Service(
            'redis',
            metadata={'name': 'redis'},
            spec={
                'ports': [{'port': REDIS_PORT}],
                'selector': redis_sts.spec.selector.match_labels,
            },
            opts=k8s_opts,
        )

        env_vars = {
            'PAPERLESS_REDIS': p.Output.format(
                'redis://{}:{}',
                redis_service.metadata.name,
                REDIS_PORT,
            ),
            'PAPERLESS_CONSUMER_POLLING': '30',
            'PAPERLESS_TASK_WORKERS': '4',
            'PAPERLESS_THREADS_PER_WORKER': '4',
            # Extend the polling delay to account for HP bitch iteratively updating its PDFs after
            # scanning each page.
            'PAPERLESS_CONSUMER_POLLING_DELAY': '30',
            # Wait up to 10min for the scan to finish
            'PAPERLESS_CONSUMER_POLLING_RETRY_COUNT': '20',
            # TODO: Switch to TLS
            'PAPERLESS_URL': f'http://paperless.{component_config.cloudflare.zone}:{PAPERLESS_PORT}',
            # https://docs.paperless-ngx.com/troubleshooting/#gunicorn-fails-to-start-with-is-not-a-valid-port-number
            'PAPERLESS_PORT': str(PAPERLESS_PORT),
            'PAPERLESS_ADMIN_USER': admin_username,
            'PAPERLESS_OCR_LANGUAGE': 'deu+eng',
        }

        config_secret = k8s.core.v1.Secret(
            'paperless-config',
            string_data={
                'PAPERLESS_SECRET_KEY': random.RandomPassword(
                    'paperless-secret-key', length=64, special=False
                ).result,
                'PAPERLESS_ADMIN_PASSWORD': admin_password,
            },
            opts=k8s_opts,
        )

        app_labels = {'app': 'paperless'}
        sts = k8s.apps.v1.StatefulSet(
            'paperless',
            metadata={'name': 'paperless'},
            spec={
                'replicas': 1,
                'selector': {'match_labels': app_labels},
                'service_name': 'paperless-headless',
                'template': {
                    'metadata': {'labels': app_labels},
                    'spec': {
                        'containers': [
                            {
                                'name': 'paperless',
                                'image': f'ghcr.io/paperless-ngx/paperless-ngx:{component_config.paperless.version}',
                                'env': [
                                    *[{'name': k, 'value': v} for k, v in env_vars.items()],
                                ],
                                'env_from': [
                                    {
                                        'secret_ref': {
                                            'name': config_secret.metadata.name,
                                        },
                                    },
                                ],
                                'ports': [{'container_port': PAPERLESS_PORT}],
                                'volume_mounts': [
                                    {
                                        'name': 'data',
                                        'mount_path': '/usr/src/paperless/data',
                                    },
                                    {
                                        'name': 'media',
                                        'mount_path': '/usr/src/paperless/media',
                                    },
                                    {
                                        'name': 'consume',
                                        'mount_path': '/usr/src/paperless/consume',
                                    },
                                ],
                            },
                        ],
                        'volumes': [
                            {
                                'name': 'consume',
                                'csi': {
                                    'driver': 'nfs.csi.k8s.io',
                                    'volume_attributes': {
                                        'server': component_config.paperless.consume_server,
                                        'share': component_config.paperless.consume_share,
                                        'mount_options': component_config.paperless.consume_mount_options,
                                    },
                                },
                            },
                        ],
                        'security_context': {
                            'fs_group': 1000,
                        },
                    },
                },
                'volume_claim_templates': [
                    {
                        'metadata': {'name': 'data'},
                        'spec': {
                            'access_modes': ['ReadWriteOnce'],
                            'resources': {'requests': {'storage': '100Gi'}},
                        },
                    },
                    {
                        'metadata': {'name': 'media'},
                        'spec': {
                            'access_modes': ['ReadWriteOnce'],
                            'resources': {'requests': {'storage': '100Gi'}},
                        },
                    },
                ],
            },
            opts=k8s_opts,
        )

        service_paperless = k8s.core.v1.Service(
            'paperless',
            metadata={'name': 'paperless'},
            spec={
                'ports': [{'port': PAPERLESS_PORT}],
                'selector': sts.spec.selector.match_labels,
                'type': 'LoadBalancer',
                'external_traffic_policy': 'Local',
            },
            opts=k8s_opts,
        )

        # Create local DNS record
        record = deploy_base.opnsense.unbound.host_override.HostOverride(
            'paperless',
            host='paperless',
            domain=component_config.cloudflare.zone,
            record_type='A',
            ipaddress=service_paperless.status.apply(
                lambda x: x['load_balancer']['ingress'][0]['ip']  # type: ignore
            ),
        )

        p.export(
            'paperless_url',
            p.Output.format('http://{}.{}:{}', record.host, record.domain, PAPERLESS_PORT),
        )

        self.register_outputs({})
