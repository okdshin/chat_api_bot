# chat_api_bot

## デプロイ方法


## App manifest

```
{
    "display_information": {
        "name": "chat-api-bot",
        "description": "channelごとにapi endpointとmodelを設定できるプロキシchat botです",
        "background_color": "#2b3c70"
    },
    "features": {
        "bot_user": {
            "display_name": "chat-api-bot",
            "always_online": false
        }
    },
    "oauth_config": {
        "scopes": {
            "bot": [
                "app_mentions:read",
                "chat:write",
                "reactions:write",
                "users.profile:read"
            ]
        }
    },
    "settings": {
        "event_subscriptions": {
            "bot_events": [
                "app_mention"
            ]
        },
        "interactivity": {
            "is_enabled": true
        },
        "org_deploy_enabled": false,
        "socket_mode_enabled": true,
        "token_rotation_enabled": false
    }
}
```
