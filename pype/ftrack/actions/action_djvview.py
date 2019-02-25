import logging
import subprocess
import sys
import os
import re
from operator import itemgetter
import ftrack_api
from pype.ftrack import BaseHandler


class DJVViewAction(BaseHandler):
    """Launch DJVView action."""
    identifier = "djvview-launch-action"
    # label = "DJV View"
    # icon = "http://a.fsdn.com/allura/p/djv/icon"
    type = 'Application'

    def __init__(self, session):
        '''Expects a ftrack_api.Session instance'''
        super().__init__(session)

        items = []
        applications = self.get_applications()
        applications = sorted(
            applications, key=lambda application: application["label"]
        )

        for application in applications:
            self.djv_path = application.get("path", None)
            applicationIdentifier = application["identifier"]
            label = application["label"]
            items.append({
                "actionIdentifier": self.identifier,
                "label": label,
                "variant": application.get("variant", None),
                "description": application.get("description", None),
                "icon": application.get("icon", "default"),
                "applicationIdentifier": applicationIdentifier
            })

        self.items = items

        if self.identifier is None:
            raise ValueError(
                'Action missing identifier.'
            )

    def is_valid_selection(self, event):
        selection = event["data"].get("selection", [])

        if not selection:
            return

        entityType = selection[0]["entityType"]

        if entityType not in ["assetversion", "task"]:
            return False

        return True

    def discover(self, event):
        """Return available actions based on *event*. """

        if not self.is_valid_selection(event):
            return

        return {
            "items": self.items
        }

    def register(self):
        '''Registers the action, subscribing the discover and launch topics.'''
        self.session.event_hub.subscribe(
            'topic=ftrack.action.discover and source.user.username={0}'.format(
                self.session.api_user
                ), self.discover
        )
        launch_subscription = (
            'topic=ftrack.action.launch'
            ' and data.actionIdentifier={0}'
            ' and source.user.username={1}'
        )
        self.session.event_hub.subscribe(
            launch_subscription.format(
                self.identifier,
                self.session.api_user
            ),
            self.launch
        )

    def get_applications(self):
        applications = []

        label = "DJVView {version}"
        versionExpression = re.compile(r"(?P<version>\d+.\d+.\d+)")
        applicationIdentifier = "djvview"
        description = "DJV View Launcher"
        icon = "http://a.fsdn.com/allura/p/djv/icon"
        expression = []
        if sys.platform == "win32":
            expression = ["C:\\", "Program Files", "djv-\d.+",
                          "bin", "djv_view.exe"]

        elif sys.platform == "darwin":
            expression = ["Application", "DJV.app", "Contents", "MacOS", "DJV"]
        # Linuxs
        else:
            expression = ["usr", "local", "djv", "djv_view"]

        pieces = expression[:]
        start = pieces.pop(0)

        if sys.platform == 'win32':
            # On Windows C: means current directory so convert roots that look
            # like drive letters to the C:\ format.
            if start and start[-1] == ':':
                start += '\\'

        if not os.path.exists(start):
            raise ValueError(
                'First part "{0}" of expression "{1}" must match exactly to an'
                ' existing entry on the filesystem.'
                .format(start, expression)
            )

        expressions = list(map(re.compile, pieces))
        expressionsCount = len(expression)-1

        for location, folders, files in os.walk(
            start, topdown=True, followlinks=True
        ):
            level = location.rstrip(os.path.sep).count(os.path.sep)
            expression = expressions[level]

            if level < (expressionsCount - 1):
                # If not yet at final piece then just prune directories.
                folders[:] = [folder for folder in folders
                              if expression.match(folder)]
            else:
                # Match executable. Note that on OSX executable might equate to
                # a folder (.app).
                for entry in folders + files:
                    match = expression.match(entry)
                    if match:
                        # Extract version from full matching path.
                        path = os.path.join(start, location, entry)
                        versionMatch = versionExpression.search(path)
                        if versionMatch:
                            version = versionMatch.group('version')

                            applications.append({
                                'identifier': applicationIdentifier.format(
                                    version=version
                                ),
                                'path': path,
                                'version': version,
                                'label': label.format(version=version),
                                'icon': icon,
                                # 'variant': variant.format(version=version),
                                'description': description
                            })
                        else:
                            self.logger.debug(
                                'Discovered application executable, but it '
                                'does not appear to o contain required version'
                                ' information: {0}'.format(path)
                            )

                # Don't descend any further as out of patterns to match.
                del folders[:]

        return applications

    def translate_event(self, session, event):
        '''Return *event* translated structure to be used with the API.'''

        selection = event['data'].get('selection', [])

        entities = list()
        for entity in selection:
            entities.append(
                (session.get(
                    self.get_entity_type(entity), entity.get('entityId')
                ))
            )

        return entities

    def get_entity_type(self, entity):
        entity_type = entity.get('entityType').replace('_', '').lower()

        for schema in self.session.schemas:
            alias_for = schema.get('alias_for')

            if (
                alias_for and isinstance(alias_for, str) and
                alias_for.lower() == entity_type
            ):
                return schema['id']

        for schema in self.session.schemas:
            if schema['id'].lower() == entity_type:
                return schema['id']

        raise ValueError(
            'Unable to translate entity type: {0}.'.format(entity_type)
        )

    def launch(self, event):
        """Callback method for DJVView action."""
        session = self.session
        entities = self.translate_event(session, event)

        # Launching application
        if "values" in event["data"]:
            filename = event['data']['values']['path']
            file_type = filename.split(".")[-1]

            # TODO Is this proper way?
            try:
                fps = int(entities[0]['custom_attributes']['fps'])
            except Exception:
                fps = 24

            # TODO issequence is probably already built-in validation in ftrack
            isseq = re.findall('%[0-9]*d', filename)
            if len(isseq) > 0:
                if len(isseq) == 1:
                    frames = []
                    padding = re.findall('%[0-9]*d', filename).pop()
                    index = filename.find(padding)

                    full_file = filename[0:index-1]
                    file = full_file.split(os.sep)[-1]
                    folder = os.path.dirname(full_file)

                    for fname in os.listdir(path=folder):
                        if fname.endswith(file_type) and file in fname:
                            frames.append(int(fname.split(".")[-2]))

                    if len(frames) > 0:
                        start = min(frames)
                        end = max(frames)

                        range = (padding % start) + '-' + (padding % end)
                        filename = re.sub('%[0-9]*d', range, filename)
                else:
                    msg = (
                        'DJV View - Filename has more than one'
                        ' sequence identifier.'
                    )
                    return {
                        'success': False,
                        'message': (msg)
                    }

            cmd = []
            # DJV path
            cmd.append(os.path.normpath(self.djv_path))
            # DJV Options Start ##############################################
            '''layer name'''
            # cmd.append('-file_layer (value)')
            ''' Proxy scale: 1/2, 1/4, 1/8'''
            cmd.append('-file_proxy 1/2')
            ''' Cache: True, False.'''
            cmd.append('-file_cache True')
            ''' Start in full screen '''
            # cmd.append('-window_fullscreen')
            ''' Toolbar controls: False, True.'''
            # cmd.append("-window_toolbar False")
            ''' Window controls: False, True.'''
            # cmd.append("-window_playbar False")
            ''' Grid overlay: None, 1x1, 10x10, 100x100.'''
            # cmd.append("-view_grid None")
            ''' Heads up display: True, False.'''
            # cmd.append("-view_hud True")
            ''' Playback: Stop, Forward, Reverse.'''
            cmd.append("-playback Forward")
            ''' Frame.'''
            # cmd.append("-playback_frame (value)")
            cmd.append("-playback_speed " + str(fps))
            ''' Timer: Sleep, Timeout. Value: Sleep.'''
            # cmd.append("-playback_timer (value)")
            ''' Timer resolution (seconds): 0.001.'''
            # cmd.append("-playback_timer_resolution (value)")
            ''' Time units: Timecode, Frames.'''
            cmd.append("-time_units Frames")
            # DJV Options End ################################################

            # PATH TO COMPONENT
            cmd.append(os.path.normpath(filename))

            # Run DJV with these commands
            subprocess.Popen(' '.join(cmd))

            return {
                'success': True,
                'message': 'DJV View started.'
            }

        if 'items' not in event["data"]:
            event["data"]['items'] = []

        try:
            for entity in entities:
                versions = []
                allowed_types = ["img", "mov", "exr"]

                if entity.entity_type.lower() == "assetversion":
                    if entity['components'][0]['file_type'] in allowed_types:
                        versions.append(entity)

                elif entity.entity_type.lower() == "task":
                    # AssetVersions are obtainable only from shot!
                    shotentity = entity['parent']

                    for asset in shotentity['assets']:
                        for version in asset['versions']:
                            # Get only AssetVersion of selected task
                            if version['task']['id'] != entity['id']:
                                continue
                            # Get only components with allowed type
                            filetype = version['components'][0]['file_type']
                            if filetype in allowed_types:
                                versions.append(version)

                # Raise error if no components were found
                if len(versions) < 1:
                    raise ValueError('There are no Asset Versions to open.')

                for version in versions:
                    for component in version['components']:
                        label = "v{0} - {1} - {2}"

                        label = label.format(
                            str(version['version']).zfill(3),
                            version['asset']['type']['name'],
                            component['name']
                        )

                        try:
                            # TODO This is proper way to get filepath!!!
                            # THIS WON'T WORK RIGHT NOW
                            location = component[
                                'component_locations'
                            ][0]['location']
                            file_path = location.get_filesystem_path(component)
                            # if component.isSequence():
                            #     if component.getMembers():
                            #         frame = int(
                            #             component.getMembers()[0].getName()
                            #         )
                            #         file_path = file_path % frame
                        except Exception:
                            # This works but is NOT proper way
                            file_path = component[
                                'component_locations'
                            ][0]['resource_identifier']

                        event["data"]["items"].append(
                            {"label": label, "value": file_path}
                        )

        except Exception as e:
            return {
                'success': False,
                'message': str(e)
            }

        return {
            "items": [
                {
                    "label": "Items to view",
                    "type": "enumerator",
                    "name": "path",
                    "data": sorted(
                        event["data"]['items'],
                        key=itemgetter("label"),
                        reverse=True
                    )
                }
            ]
        }


def register(session):
    """Register hooks."""
    if not isinstance(session, ftrack_api.session.Session):
        return

    action = DJVViewAction(session)
    action.register()


def main(arguments=None):
    '''Set up logging and register action.'''
    if arguments is None:
        arguments = []

    import argparse
    parser = argparse.ArgumentParser()
    # Allow setting of logging level from arguments.
    loggingLevels = {}
    for level in (
        logging.NOTSET, logging.DEBUG, logging.INFO, logging.WARNING,
        logging.ERROR, logging.CRITICAL
    ):
        loggingLevels[logging.getLevelName(level).lower()] = level

    parser.add_argument(
        '-v', '--verbosity',
        help='Set the logging output verbosity.',
        choices=loggingLevels.keys(),
        default='info'
    )
    namespace = parser.parse_args(arguments)

    # Set up basic logging
    logging.basicConfig(level=loggingLevels[namespace.verbosity])

    session = ftrack_api.Session()
    register(session)

    # Wait for events
    logging.info(
        'Registered actions and listening for events. Use Ctrl-C to abort.'
    )
    session.event_hub.wait()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))