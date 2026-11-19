# Back in Frame templates for Unraid

Templates for Unraid's Community Applications. This folder is meant to become its own repository,
`github.com/backinframe/unraid-templates`, because Community Applications reads a whole repository:

```
ca_profile.xml              maintainer profile (required: a non-empty <Profile>)
LICENSE                     OSI license (MIT)
icon.png                    512×512 icon, also used by the template
templates/backinframe.xml
```

## Before submitting

1. Create the repository and copy this folder's contents into its root.
2. Post the support thread (text in `support-thread.md`) on forums.unraid.net under
   *Docker Containers*, and replace `SUPPORT-THREAD-BACKINFRAME` in `ca_profile.xml` and
   `templates/backinframe.xml` with the thread's address.
3. Check the files: `xmllint --noout ca_profile.xml templates/backinframe.xml`.
4. Check that the image exists: `docker pull ghcr.io/backinframe/backinframe:latest`.
5. Install the template by hand once on a test server (copy it to
   `/boot/config/plugins/dockerMan/templates-user/`), go through the first start, then submit the
   repository at https://ca.unraid.net/submit.

After a release, update `<Changes>` in the template.
