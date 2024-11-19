import argparse
import os
import shlex
import time
import dataclasses
from dataclasses import dataclass, fields

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.http_retry import all_builtin_retry_handlers

from sqlalchemy.sql import exists
from sqlalchemy import create_engine, Integer, Column, Float, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from openai import OpenAI


ENV_VAR_PREFIX = "CHAT_API_BOT"


@dataclass
class ChatCompletionsOptions:
    base_url: str
    model: str
    role: str = "user"
    temperature: float = 1.0  # OpenAI API default
    top_p: float = 1.0  # OpenAI API default

    broadcast_reply: int = 1


Base = declarative_base()


class ChannelChatCompletionsOptions(Base):
    __tablename__ = 'channel_chat_completions_options'
    channel = Column(String, primary_key=True)


for field in fields(ChatCompletionsOptions):
    if field.type == str:
        column_type = String
    elif field.type == float:
        column_type = Float
    elif field.type == int:
        column_type = Integer
    else:
        raise NotImplementedError
    setattr(
        ChannelChatCompletionsOptions,
        field.name,
        Column(column_type)
    )


engine = create_engine('sqlite:///example.db', echo=True)
Base.metadata.create_all(engine)


def setup_chat_completions_options_parser(parser, channel_defaults=None, cli_defaults=None):
    for field in fields(ChatCompletionsOptions):
        assert field.default is dataclasses.MISSING or field.default != field.default_factory
        app_default = field.default if field.default is not dataclasses.MISSING else None
        if channel_defaults is not None:
            assert cli_defaults is not None
            if getattr(channel_defaults, field.name) is not None:
                default = getattr(channel_defaults, field.name)
            elif getattr(cli_defaults, field.name) is not None:
                default = getattr(cli_defaults, field.name)
            else:
                default = app_default
        else:
            default = app_default
        parser.add_argument(
            f"--{field.name.replace('_', '-')}",
            type=field.type,
            default=default,
            help="-",
        )


cli_parser = argparse.ArgumentParser(
    prog=ENV_VAR_PREFIX.lower(),
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
setup_chat_completions_options_parser(cli_parser)


def parse_key_value_pair(string):
    try:
        key, value = string.split('=')
        return key, value
    except ValueError:
        msg = f"'{string}' is not formatted like 'key=value'"
        raise argparse.ArgumentTypeError(msg)


cli_parser.add_argument(
    '--api-endpoint-and-api-key-env-var-pair-list',
    nargs='+',
    type=parse_key_value_pair,
    metavar='KEY=VALUE',
    default=[],
    help="-")
cli_parser.add_argument(
    '--typing-emoji',
    default=":keyboard:",
    type=str,
    help="-")
cli_args = cli_parser.parse_args()


api_endpoint_and_api_key_env_var_dict = dict(cli_args.api_endpoint_and_api_key_env_var_pair_list)


load_dotenv(verbose=True)  # Load slack bot/app token and api_key


app = App(
    client=WebClient(
        token=os.getenv(f"{ENV_VAR_PREFIX}_SLACK_BOT_TOKEN"),
        retry_handlers=all_builtin_retry_handlers(),
    ),
)


def dummy_text_iterator(text: str):
    for char in list(text):
        time.sleep(0.1)
        yield char


def reply_message(message, event, reply_broadcast=True):
    app.client.chat_postMessage(
        text=message,
        thread_ts=event["ts"],
        channel=event["channel"],
        reply_broadcast=reply_broadcast,
    )


def reply_streaming_message(message_iterator, event, reply_broadcast=True):
    message_buffer = []
    message_buffer.append(message_iterator.__next__())
    initial_post_result = app.client.chat_postMessage(
        text="".join(message_buffer)+cli_args.typing_emoji,
        thread_ts=event["ts"],
        channel=event["channel"],
        reply_broadcast=reply_broadcast,
    )
    last_update = time.perf_counter()
    for partial_message in message_iterator:
        message_buffer.append(partial_message)
        if time.perf_counter() - last_update > 1.0:
            app.client.chat_update(
                text="".join(message_buffer)+cli_args.typing_emoji,
                ts=initial_post_result["ts"],
                channel=event["channel"],
            )
            last_update = time.perf_counter()
    time.sleep(1)
    app.client.chat_update(
        text="".join(message_buffer),
        ts=initial_post_result["ts"],
        channel=event["channel"],
    )


@app.event("app_mention")
def app_mention(event, say):
    parser = argparse.ArgumentParser(
        prog=ENV_VAR_PREFIX.lower(),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=False,
    )
    Session = sessionmaker(bind=engine)
    session = Session()
    o = session.query(ChannelChatCompletionsOptions).filter(ChannelChatCompletionsOptions.channel==event["channel"]).first()
    channel_defaults = (
        o or ChannelChatCompletionsOptions()
    )
    print("Channel defaults!", channel_defaults)
    parser.add_argument("content", type=str, nargs="?", default="", help="-")
    setup_chat_completions_options_parser(
        parser=parser,
        channel_defaults=channel_defaults,
        cli_defaults=cli_args,
    )
    parser.add_argument("--set-as-channel-defaults", action="store_true", help="-")
    try:
        args = parser.parse_args(shlex.split(event["text"])[1:])
        print(args)
        if args.content == "" and not args.set_as_channel_defaults:
            reply_message(message=parser.format_help(), event=event, reply_broadcast=False)
            return
        else:
            args.content = event["text"][len(shlex.split(event["text"])[0]+" "):]
        args.base_url = args.base_url.lstrip("<").rstrip(">")
    except (argparse.ArgumentError, argparse.ArgumentTypeError) as e:
        reply_message(message=e, event=event, reply_broadcast=False)
        return

    if args.set_as_channel_defaults:
        new_channel_defaults_dict = {
            field.name: getattr(args, field.name)
            for field in fields(ChatCompletionsOptions)
        }
        Session = sessionmaker(bind=engine)
        session = Session()
        if session.query(exists().where(ChannelChatCompletionsOptions.channel==event["channel"])).scalar():
            channel_defaults = session.query(ChannelChatCompletionsOptions).filter(ChannelChatCompletionsOptions.channel==event["channel"]).first()
            for key, value in new_channel_defaults_dict.items():
                setattr(channel_defaults, key, value)
        else:
            new_channel_defaults = ChannelChatCompletionsOptions(
                **new_channel_defaults_dict,
                channel=event["channel"],
            )
            session.add(new_channel_defaults)
        session.commit()
        reply_message(message=f"{new_channel_defaults_dict}", event=event)
        return

    if args.base_url in api_endpoint_and_api_key_env_var_dict:
        api_key_env_var = api_endpoint_and_api_key_env_var_dict[args.base_url]
        api_key = os.getenv(api_key_env_var)
        if api_key is None:
            reply_message(message=f"api_key environment variable \"{api_key_env_var}\" is not found", event=event)
            return
    else:
        api_key = "dummy"

    print(args)

    try:
        openai_api_client = OpenAI(base_url=args.base_url, api_key=api_key)
        stream = openai_api_client.chat.completions.create(
            model=args.model,
            messages=[{"role": args.role, "content": args.content}],
            stream=True,
            temperature=args.temperature,
            top_p=args.top_p,
        )

        def streaming_response():
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content

        reply_streaming_message(
            #message_iterator=dummy_text_iterator(f"hello {args}"),
            message_iterator=streaming_response(),
            event=event,
            reply_broadcast=args.broadcast_reply,
        )
    except Exception as e:
        print(e)
        reply_message(message=f"{repr(e)}", event=event, reply_broadcast=False)


if __name__ == "__main__":
    SocketModeHandler(app, os.getenv(f"{ENV_VAR_PREFIX}_SLACK_APP_TOKEN")).start()
