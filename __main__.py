import pulumi as p
import pulumi_kubernetes as k8s

from paperless.config import ComponentConfig

config = p.Config()
component_config = ComponentConfig.model_validate(config.get_object('config'))

k8s_provider = k8s.Provider('k8s', kubeconfig=component_config.kubeconfig.value)
