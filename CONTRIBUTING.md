# Contributing to APISwitch

First off, thank you for considering contributing to APISwitch! It's people like you that make open source such a great community.

## Where do I go from here?

If you've noticed a bug or have a feature request, [make one](https://github.com/methetech/apiswitch/issues/new)! It's generally best if you get confirmation of your bug or approval for your feature request this way before starting to code.

## Fork & create a branch

If this is something you think you can fix, then [fork APISwitch](https://github.com/methetech/apiswitch/fork) and create a branch with a descriptive name.

A good branch name would be (where issue #38 is the ticket you're working on):

```
git checkout -b 38-add-awesome-new-feature
```

## Get the test suite running

Make sure you get the test suite running on your local machine.

## Implement your fix or feature

At this point, you're ready to make your changes! Feel free to ask for help; everyone is a beginner at first :smile_cat:

## Make a Pull Request

At this point, you should switch back to your master branch and make sure it's up to date with APISwitch's master branch:

```
git remote add upstream git@github.com:methetech/apiswitch.git
git checkout master
git pull upstream master
```

Then update your feature branch from your local copy of master, and push it!

```
git checkout 38-add-awesome-new-feature
git rebase master
git push --force-with-lease origin 38-add-awesome-new-feature
```

Finally, go to GitHub and [make a Pull Request](https://github.com/methetech/apiswitch/compare) :D

## Keeping your Pull Request updated

If a maintainer asks you to "rebase" your PR, they're saying that a lot of code has changed, and that you need to update your branch so it's easier to merge.

To learn more about rebasing and merging, check out this guide on [merging vs. rebasing](https://www.atlassian.com/git/tutorials/merging-vs-rebasing).

Once you've updated your branch, you'll need to force push the changes to your remote branch.

```
git push --force-with-lease origin 38-add-awesome-new-feature
```

## Merging a PR (for maintainers)

A PR can be merged into the master branch by a maintainer if it has been approved by at least one other maintainer.

## Shipping a new release (for maintainers)

A new release can be shipped by a maintainer after a PR has been merged into the master branch.

```
npm version patch -m "Upgrade to %s for reasons"
```

This will create a new commit and a new tag.

```
git push
git push --tags
```

Now, go to GitHub and [create a new release](https://github.com/methetech/apiswitch/releases/new).

That's it! You've successfully contributed to APISwitch.
