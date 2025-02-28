import pathlib

import deploy_base.model
import pydantic

REPO_PREFIX = 'deploy-'


def get_pulumi_project():
    repo_dir = pathlib.Path().resolve()

    while not repo_dir.name.startswith(REPO_PREFIX):
        if not repo_dir.parents:
            raise ValueError('Could not find repo root')

        repo_dir = repo_dir.parent
    return repo_dir.name[len(REPO_PREFIX) :]


class RedisConfig(deploy_base.model.LocalBaseModel):
    version: str


class PaperlessConfig(deploy_base.model.LocalBaseModel):
    version: str

    consume_server: str = pydantic.Field(alias='consume-server')
    consume_share: str = pydantic.Field(alias='consume-share')
    consume_mount_options: str = pydantic.Field(
        alias='consume-mount-options', default='nfsvers=4.1,sec=sys'
    )


class ComponentConfig(deploy_base.model.LocalBaseModel):
    kubeconfig: deploy_base.model.OnePasswordRef
    cloudflare: deploy_base.model.CloudflareConfig
    paperless: PaperlessConfig
    redis: RedisConfig


class StackConfig(deploy_base.model.LocalBaseModel):
    model_config = {'alias_generator': lambda field_name: f'{get_pulumi_project()}:{field_name}'}
    config: ComponentConfig


class PulumiConfigRoot(deploy_base.model.LocalBaseModel):
    config: StackConfig
